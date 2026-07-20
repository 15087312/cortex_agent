"""
部署脚本 - 本地部署
"""
import os
import subprocess


def deploy_local():
    """本地部署"""
    print("Starting local deployment...")
    subprocess.run(["python", "main.py"])
    print("Local deployment completed.")


if __name__ == "__main__":
    deploy_local()