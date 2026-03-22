"""
监控系统主入口

提供多种启动方式：CLI、API服务器、后台服务。
"""

import sys
import argparse
from typing import List

from .cli import main as cli_main
from .api import run_api_server
from .service import create_monitoring_service
from ..utils.logging import setup_logging, get_logger

logger = get_logger(__name__)


def run_cli(args: List[str] = None):
    """运行CLI"""
    if args is None:
        args = sys.argv[1:]
    
    # 添加子命令前缀
    cli_args = ["monitoring"] + args
    sys.argv = [sys.argv[0]] + cli_args
    
    cli_main()


def run_api(args: List[str] = None):
    """运行API服务器"""
    parser = argparse.ArgumentParser(description="运行监控API服务器")
    parser.add_argument("--host", default="0.0.0.0", help="主机地址")
    parser.add_argument("--port", type=int, default=5000, help="端口号")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    
    if args is None:
        args = sys.argv[1:]
    
    parsed_args = parser.parse_args(args)
    
    logger.info(f"启动监控API服务器: {parsed_args.host}:{parsed_args.port}")
    run_api_server(
        host=parsed_args.host,
        port=parsed_args.port,
        debug=parsed_args.debug
    )


def run_service(args: List[str] = None):
    """运行为后台服务"""
    parser = argparse.ArgumentParser(description="运行监控后台服务")
    parser.add_argument("--config-dir", help="配置目录路径")
    
    if args is None:
        args = sys.argv[1:]
    
    parsed_args = parser.parse_args(args)
    
    # 创建并启动服务
    service = create_monitoring_service(parsed_args.config_dir)
    
    logger.info("启动监控后台服务")
    service.start()
    
    try:
        # 保持服务运行
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到中断信号，停止服务")
        service.stop()


def main():
    """主函数"""
    # 设置日志
    setup_logging()
    
    parser = argparse.ArgumentParser(description="监控告警系统")
    subparsers = parser.add_subparsers(dest="mode", help="运行模式")
    
    # CLI模式
    cli_parser = subparsers.add_parser("cli", help="命令行界面")
    cli_parser.add_argument("cli_args", nargs=argparse.REMAINDER, help="CLI参数")
    
    # API模式
    api_parser = subparsers.add_parser("api", help="API服务器")
    api_parser.add_argument("api_args", nargs=argparse.REMAINDER, help="API服务器参数")
    
    # 服务模式
    service_parser = subparsers.add_parser("service", help="后台服务")
    service_parser.add_argument("service_args", nargs=argparse.REMAINDER, help="服务参数")
    
    # 如果没有参数，显示帮助
    if len(sys.argv) == 1:
        parser.print_help()
        return
    
    args = parser.parse_args()
    
    if args.mode == "cli":
        run_cli(args.cli_args)
    elif args.mode == "api":
        run_api(args.api_args)
    elif args.mode == "service":
        run_service(args.service_args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()