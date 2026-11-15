"""后端常驻麦克风监听 — pyaudio 采集 → VAD 断句 → ASR

设计要点（替代「浏览器拿麦克风」的方案）：
- 后端直接读本机麦克风，桌宠窗口/网页都不用开麦，也不会因为 QWebEngine
  权限拒绝而静默失败
- 能量 VAD（自适应噪声底）+ 静音断句：Silero 需要从 GitHub 拉模型，
  离线环境下不可用，这里用纯 numpy 实现，保证任何机器都能跑
- 断句后的音频交给单线程 ASR worker，音频采集线程永不阻塞
- 检测到人声立即回调 on_speech_start（打断正在播放的 TTS）
"""
import queue
import threading
import time
from typing import Callable, Optional

from utils.logger import setup_logger

logger = setup_logger("pet_mic_listener")


class BackendMicListener:
    """后端常驻麦克风监听

    回调（都在采集/识别线程里调用，调用方自行切线程）：
    - on_utterance(text): 一句话识别完成
    - on_speech_start(): 检测到人声（用于打断 TTS 播放）
    """

    FRAME_MS = 30            # 每帧 30ms，能量 VAD 的常见帧长
    PREROLL_MS = 300         # 断句保留的语音前导，避免吃掉第一个字
    ONSET_FRAMES = 3         # 连续 3 帧（90ms）才算真正开口，滤掉点击/喷嚏
    MIN_VOICED_FRAMES = 8    # 一段话至少 240ms 有声，否则丢弃（滤掉噪音气泡）
    NOISE_EMA = 0.05         # 噪声底跟踪系数（仅非语音帧更新，~0.6s 收敛）

    def __init__(
        self,
        on_utterance: Callable[[str], None],
        on_speech_start: Optional[Callable[[], None]] = None,
        sample_rate: int = 16000,
        energy_threshold: Optional[float] = None,
        silence_duration: Optional[float] = None,
        max_utterance_seconds: Optional[float] = None,
        language: Optional[str] = None,
        input_device: Optional[int] = None,
        calibration_ms: Optional[float] = None,
    ):
        self._on_utterance = on_utterance
        self._on_speech_start = on_speech_start
        self._sample_rate = int(sample_rate)
        self._frame = int(self._sample_rate * self.FRAME_MS / 1000)
        self._language = language
        self._input_device = input_device

        try:
            from config.settings import settings
            self._energy_threshold = float(
                energy_threshold if energy_threshold is not None
                else getattr(settings, "PERCEPTION_VOICE_ENERGY_THRESHOLD", 300) or 300
            )
            self._silence_duration = float(
                silence_duration if silence_duration is not None
                else getattr(settings, "PERCEPTION_VOICE_SILENCE_DURATION", 1.5) or 1.5
            )
            self._max_utterance = float(
                max_utterance_seconds if max_utterance_seconds is not None
                else getattr(settings, "PERCEPTION_VOICE_MAX_DURATION", 60) or 60
            )
        except Exception:
            self._energy_threshold = float(energy_threshold or 300)
            self._silence_duration = float(silence_duration or 1.5)
            self._max_utterance = float(max_utterance_seconds or 60)

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._asr_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=8)
        self._asr_thread: Optional[threading.Thread] = None
        self._stream = None
        self._pa = None
        self._lock = threading.Lock()
        self._state = "idle"           # idle / listening / transcribing
        self.last_error: str = ""
        # 自适应噪声底（int16 RMS）：第一帧作为初值，之后只由非语音帧缓慢跟踪。
        # 固定能量阈值（默认 300）在真实环境下几乎总是低于环境噪声
        # （实测本机环境 RMS 中位数 ~375），会导致「一直在听」的假触发。
        self._noise_floor = 0.0
        # 启动后先静默采样一小段环境音（中位数）作为噪声底，避免开局阈值偏低
        self._calibration_frames = int(max(0.0, float(calibration_ms if calibration_ms is not None else 700)) / self.FRAME_MS)
        self._calib_rms: list = []
        self._heard_anything = False   # 本次 start 后是否识别到过文字（测试/排查用）

    # ── 属性 ──

    @property
    def running(self) -> bool:
        return self._running

    @property
    def state(self) -> str:
        return self._state

    def status(self) -> dict:
        return {
            "running": self._running,
            "state": self._state,
            "last_error": self.last_error,
            "energy_threshold": round(self._threshold(), 1),
            "noise_floor": round(self._noise_floor, 1),
            "calibrating": len(self._calib_rms) > 0 and self._noise_floor <= 0,
            "silence_duration": self._silence_duration,
            "heard_anything": self._heard_anything,
        }

    # ── 生命周期 ──

    def start(self) -> bool:
        """打开麦克风并开始监听；失败返回 False（原因见 last_error）"""
        with self._lock:
            if self._running:
                return True
            try:
                import pyaudio
            except ImportError:
                self.last_error = "pyaudio 未安装（pip install pyaudio）"
                logger.warning(self.last_error)
                return False
            try:
                pa = pyaudio.PyAudio()
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=self._sample_rate,
                    input=True,
                    frames_per_buffer=self._frame,
                    input_device_index=self._input_device,
                )
            except Exception as e:
                self.last_error = f"麦克风打开失败: {e}"
                logger.warning(self.last_error)
                try:
                    pa.terminate()  # type: ignore[possibly-undefined]
                except Exception:
                    pass
                return False

            self._pa = pa
            self._stream = stream
            self._running = True
            self._state = "idle"
            self.last_error = ""
            self._heard_anything = False
            self._thread = threading.Thread(target=self._capture_loop, name="pet-mic", daemon=True)
            self._thread.start()
            self._asr_thread = threading.Thread(target=self._asr_loop, name="pet-mic-asr", daemon=True)
            self._asr_thread.start()
            logger.info(
                f"麦克风监听已启动: {self._sample_rate}Hz "
                f"能量阈值={self._energy_threshold:.0f} 静音断句={self._silence_duration}s"
            )
            return True

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        # 顺序很重要（实测 macOS）：先等采集线程自己退出，再关流/pyaudio。
        # - 先 stop_stream()：正在 read() 的线程会永久阻塞在「不会再来的下一帧」
        # - 先 terminate()：线程踩到已释放的 PortAudio 对象 → Bus error 带走进程
        # 设置 _running=False 后，线程会在下一帧（约 30ms）自然退出。
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        if thread is not None and thread.is_alive():
            # 仍卡在 read()：只能靠 stop_stream 打断，再给它一点时间
            try:
                if self._stream is not None:
                    self._stream.stop_stream()
            except Exception as e:
                logger.debug(f"停止麦克风流失败: {e}")
            thread.join(timeout=1.0)
            if thread.is_alive():
                logger.warning("麦克风采集线程未在 2s 内退出，跳过关闭设备以免崩溃")
                self._stream = None
                self._pa = None
                self._thread = None
                self._state = "idle"
                return
        try:
            if self._stream is not None:
                self._stream.close()
        except Exception as e:
            logger.debug(f"关闭麦克风流失败: {e}")
        try:
            if self._pa is not None:
                self._pa.terminate()
        except Exception:
            pass
        self._stream = None
        self._pa = None
        self._thread = None
        try:
            self._asr_queue.put_nowait(b"")
        except queue.Full:
            pass
        self._state = "idle"
        logger.info("麦克风监听已停止")

    # ── 采集线程 ──

    def _rms(self, data: bytes) -> float:
        try:
            import numpy as np
            arr = np.frombuffer(data, dtype=np.int16)
            if arr.size == 0:
                return 0.0
            return float(np.sqrt(np.mean(arr.astype(np.float64) ** 2)))
        except Exception:
            return 0.0

    def _threshold(self) -> float:
        # 自适应：噪声底 ×2.2 与配置下限取大者，既能安静房间灵敏，
        # 也不至于把风扇/键盘当成人声（上限 8000 防开局就有人说话时锁死）
        if self._noise_floor <= 0:
            return self._energy_threshold
        return max(self._energy_threshold, min(self._noise_floor * 2.2, 8000.0))

    def _capture_loop(self) -> None:
        frame_bytes = self._frame * 2
        preroll_frames = max(1, int(self.PREROLL_MS / self.FRAME_MS))
        preroll: list = []
        utterance: list = []
        speaking = False
        voice_run = 0          # 连续有声帧数（起音去抖）
        voiced_frames = 0      # 本段语音的有声帧数（最短语音长度）
        last_voice_at = 0.0
        started_at = 0.0

        while self._running:
            # 本地取一次引用：stop() 会把 _stream 置 None，直接 self._stream.read
            # 会在关麦瞬间 AttributeError（或踩到已释放的 PortAudio 对象）
            stream = self._stream
            if stream is None:
                break
            try:
                data = stream.read(self._frame, exception_on_overflow=False)
            except Exception as e:
                if self._running:
                    logger.debug(f"读取麦克风失败: {e}")
                    time.sleep(0.1)
                continue
            if not data:
                continue
            if len(data) < frame_bytes:
                data = data.ljust(frame_bytes, b"\x00")

            rms = self._rms(data)
            # 启动校准：前 N 帧不管响度都当环境音，取中位数作噪声底
            if self._noise_floor <= 0:
                if self._calibration_frames > 0:
                    self._calib_rms.append(rms)
                    if len(self._calib_rms) >= self._calibration_frames:
                        ordered = sorted(self._calib_rms)
                        self._noise_floor = max(ordered[len(ordered) // 2], 1.0)
                        self._calib_rms = []
                    continue
                self._noise_floor = max(rms, 1.0)
            thr = self._threshold()
            voice = rms >= thr

            if not speaking:
                # 未在说话：维护前导缓冲 + 跟踪噪声底
                preroll.append(data)
                if len(preroll) > preroll_frames:
                    preroll.pop(0)
                if voice:
                    voice_run += 1
                else:
                    voice_run = 0
                    # 下限 1.0：长时间绝对安静时 EMA 会一路衰减到次正规数（甚至 0），
                    # 阈值随之退回配置下限，反而更容易误触发
                    self._noise_floor = max(
                        1.0,
                        (1 - self.NOISE_EMA) * self._noise_floor + self.NOISE_EMA * rms,
                    )
                if voice_run >= self.ONSET_FRAMES:
                    speaking = True
                    voice_run = 0
                    voiced_frames = 0
                    started_at = time.time()
                    last_voice_at = started_at
                    utterance = list(preroll)
                    preroll = []
                    self._state = "listening"
                    if self._on_speech_start:
                        try:
                            self._on_speech_start()
                        except Exception as e:
                            logger.debug(f"on_speech_start 回调失败: {e}")
                continue

            # 正在说话
            utterance.append(data)
            if voice:
                voiced_frames += 1
            now = time.time()
            if voice:
                last_voice_at = now
            silence = now - last_voice_at
            too_long = (now - started_at) >= self._max_utterance
            if silence >= self._silence_duration or too_long:
                speaking = False
                voice_run = 0
                pcm = b"".join(utterance)
                utterance = []
                self._state = "idle"
                if voiced_frames < self.MIN_VOICED_FRAMES:
                    logger.debug(
                        f"丢弃过短语音段（{voiced_frames} 帧有声 < {self.MIN_VOICED_FRAMES}）"
                    )
                    continue
                try:
                    self._asr_queue.put_nowait(pcm)
                except queue.Full:
                    logger.warning("语音识别队列已满，丢弃这一段音频")

    # ── 识别线程 ──

    def _asr_loop(self) -> None:
        from modules.desktop_pet.voice.asr import transcribe_pcm

        while True:
            try:
                pcm = self._asr_queue.get(timeout=0.5)
            except queue.Empty:
                if not self._running:
                    # 退出前把剩余音频处理完
                    if self._asr_queue.empty():
                        return
                continue
            if not pcm:
                return
            self._state = "transcribing"
            try:
                text = transcribe_pcm(
                    pcm,
                    sample_rate=self._sample_rate,
                    language=self._language,
                )
            except Exception as e:
                logger.debug(f"语音识别异常: {e}")
                text = None
            finally:
                self._state = "idle"
            if text:
                self._heard_anything = True
                logger.info(f"麦克风识别: {text[:80]}")
                try:
                    self._on_utterance(text)
                except Exception as e:
                    logger.debug(f"on_utterance 回调失败: {e}")
