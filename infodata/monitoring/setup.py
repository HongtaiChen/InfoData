            # 删除README.md
            readme_file = self.base_dir / "README.md"
            if readme_file.exists():
                readme_file.unlink()
                logger.info(f"删除文件: {readme_file}")
            
            logger.info("监控告警系统卸载完成")
            
        except Exception as e:
            logger.error(f"卸载失败: {e}")
            raise


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="监控告警系统安装工具")
    parser.add_argument("command", choices=["install", "uninstall", "setup-dirs", "setup-config", 
                                           "setup-scripts", "create-readme"], 
                       help="安装命令")
    parser.add_argument("--base-dir", help="基础目录路径")
    
    args = parser.parse_args()
    
    # 设置日志
    from ..utils.logging import setup_logging
    setup_logging()
    
    # 创建安装器
    setup = MonitoringSetup(args.base_dir)
    
    # 执行命令
    if args.command == "install":
        setup.install()
    elif args.command == "uninstall":
        setup.uninstall()
    elif args.command == "setup-dirs":
        setup.setup_directories()
    elif args.command == "setup-config":
        setup.setup_config_files()
    elif args.command == "setup-scripts":
        setup.setup_startup_scripts()
    elif args.command == "create-readme":
        setup.create_readme()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()