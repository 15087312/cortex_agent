"""
数据清理脚本 - 清理过期记忆、临时文件、旧日志
"""
import os
import shutil
from datetime import datetime, timedelta


def cleanup_temp(temp_dir: str = "data/temp", max_age_days: int = 7):
    """清理临时文件"""
    print(f"Cleaning up temporary files older than {max_age_days} days...")
    
    cutoff = datetime.now() - timedelta(days=max_age_days)
    
    if not os.path.exists(temp_dir):
        return
    
    for root, dirs, files in os.walk(temp_dir):
        for file in files:
            file_path = os.path.join(root, file)
            file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
            
            if file_time < cutoff:
                os.remove(file_path)
                print(f"Deleted: {file_path}")


def cleanup_logs(log_dir: str = "data/logs", max_age_days: int = 30):
    """清理旧日志"""
    print(f"Cleaning up logs older than {max_age_days} days...")
    
    cutoff = datetime.now() - timedelta(days=max_age_days)
    
    if not os.path.exists(log_dir):
        return
    
    for root, dirs, files in os.walk(log_dir):
        for file in files:
            if file.endswith('.log'):
                file_path = os.path.join(root, file)
                file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                
                if file_time < cutoff:
                    os.remove(file_path)
                    print(f"Deleted: {file_path}")


def cleanup_cache(cache_dir: str = "data/cache", max_age_days: int = 3):
    """清理缓存"""
    print(f"Cleaning up cache older than {max_age_days} days...")
    
    cutoff = datetime.now() - timedelta(days=max_age_days)
    
    if not os.path.exists(cache_dir):
        return
    
    for root, dirs, files in os.walk(cache_dir):
        for file in files:
            file_path = os.path.join(root, file)
            file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
            
            if file_time < cutoff:
                os.remove(file_path)
                print(f"Deleted: {file_path}")


def main():
    """主函数"""
    print("Starting cleanup process...")
    
    cleanup_temp()
    cleanup_logs()
    cleanup_cache()
    
    print("Cleanup completed.")


if __name__ == "__main__":
    main()

