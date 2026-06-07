"""
硬件检测模块 - 跨设备自适应基础
"""
import psutil
import platform
from utils.logger import setup_logger

logger = setup_logger("hardware_detector")


class HardwareDetector:
    @staticmethod
    def detect():
        total_memory = psutil.virtual_memory().total // (1024**3)
        available_memory = psutil.virtual_memory().available // (1024**3)
        system = platform.system()
        is_apple_silicon = platform.machine() == 'arm64' and system == 'Darwin'
        cpu_count = psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True)
        
        if total_memory <= 8:
            strategy = "low"
        elif total_memory <= 16:
            strategy = "mid"
        elif total_memory <= 24:
            strategy = "high"
        else:
            strategy = "extreme"
        
        hardware_info = {
            "total_memory_gb": total_memory,
            "available_memory_gb": available_memory,
            "system": system,
            "is_apple_silicon": is_apple_silicon,
            "cpu_count": cpu_count,
            "resource_strategy": strategy
        }
        
        logger.info(f"硬件检测完成: {total_memory}GB内存, {strategy}策略")
        return hardware_info
