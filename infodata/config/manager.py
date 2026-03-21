"""
配置管理器

负责配置的加载、验证、热更新和环境变量支持。
"""

import os
import yaml
from pathlib import Path
from typing import Any, Dict, Optional
from pydantic import ValidationError
from .schemas import AppConfig


class ConfigManager:
    """配置管理器"""
    
    _instance: Optional["ConfigManager"] = None
    _config: Optional[AppConfig] = None
    _config_path: Optional[Path] = None
    _last_modified: float = 0
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def load(cls, config_path: Optional[str] = None) -> AppConfig:
        """
        加载配置文件
        
        Args:
            config_path: 配置文件路径，如果为None则使用默认路径
            
        Returns:
            AppConfig: 配置对象
            
        Raises:
            FileNotFoundError: 配置文件不存在
            ValidationError: 配置验证失败
        """
        if config_path is None:
            config_path = cls._get_default_config_path()
        
        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        
        # 读取配置文件
        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f) or {}
        
        # 应用环境变量覆盖
        config_data = cls._apply_env_overrides(config_data)
        
        # 验证配置
        try:
            config = AppConfig(**config_data)
        except ValidationError as e:
            raise ValidationError(f"配置验证失败: {e}", model=AppConfig)
        
        # 更新实例状态
        cls._instance._config = config
        cls._instance._config_path = config_file
        cls._instance._last_modified = config_file.stat().st_mtime
        
        return config
    
    @classmethod
    def reload(cls) -> AppConfig:
        """
        重新加载配置文件
        
        Returns:
            AppConfig: 新的配置对象
            
        Raises:
            RuntimeError: 配置未加载
            FileNotFoundError: 配置文件不存在
            ValidationError: 配置验证失败
        """
        if cls._instance is None or cls._instance._config_path is None:
            raise RuntimeError("配置未加载，请先调用load()方法")
        
        return cls.load(str(cls._instance._config_path))
    
    @classmethod
    def check_for_updates(cls) -> bool:
        """
        检查配置文件是否有更新
        
        Returns:
            bool: 是否有更新
        """
        if cls._instance is None or cls._instance._config_path is None:
            return False
        
        config_file = cls._instance._config_path
        if not config_file.exists():
            return False
        
        current_mtime = config_file.stat().st_mtime
        return current_mtime > cls._instance._last_modified
    
    @classmethod
    def get_config(cls) -> AppConfig:
        """
        获取当前配置
        
        Returns:
            AppConfig: 当前配置对象
            
        Raises:
            RuntimeError: 配置未加载
        """
        if cls._instance is None or cls._instance._config is None:
            raise RuntimeError("配置未加载，请先调用load()方法")
        
        return cls._instance._config
    
    @classmethod
    def _get_default_config_path(cls) -> str:
        """
        获取默认配置文件路径
        
        Returns:
            str: 默认配置文件路径
        """
        # 1. 环境变量指定的路径
        env_path = os.getenv("INFODATA_CONFIG_PATH")
        if env_path:
            return env_path
        
        # 2. 当前目录下的config.yaml
        current_dir = Path.cwd()
        config_files = [
            current_dir / "config.yaml",
            current_dir / "config.yml",
            current_dir / "config" / "config.yaml",
        ]
        
        for config_file in config_files:
            if config_file.exists():
                return str(config_file)
        
        # 3. 用户主目录下的配置文件
        home_dir = Path.home()
        home_config = home_dir / ".config" / "infodata" / "config.yaml"
        if home_config.exists():
            return str(home_config)
        
        # 4. 使用默认配置
        return str(current_dir / "config.yaml")
    
    @classmethod
    def _apply_env_overrides(cls, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        应用环境变量覆盖
        
        Args:
            config_data: 原始配置数据
            
        Returns:
            Dict[str, Any]: 应用环境变量后的配置数据
        """
        # 数据库配置环境变量
        if "database" in config_data:
            db_config = config_data["database"]
            if "mysql" in db_config:
                mysql_config = db_config["mysql"]
                mysql_config["host"] = os.getenv("DB_HOST", mysql_config.get("host", "localhost"))
                mysql_config["port"] = int(os.getenv("DB_PORT", mysql_config.get("port", 3306)))
                mysql_config["user"] = os.getenv("DB_USER", mysql_config.get("user", "root"))
                mysql_config["password"] = os.getenv("DB_PASSWORD", mysql_config.get("password", ""))
                mysql_config["database"] = os.getenv("DB_DATABASE", mysql_config.get("database", "infodata"))
        
        # 数据源配置环境变量
        if "data_sources" in config_data:
            ds_config = config_data["data_sources"]
            if "tushare" in ds_config:
                tushare_config = ds_config["tushare"]
                tushare_config["token"] = os.getenv("TUSHARE_TOKEN", tushare_config.get("token", ""))
        
        # 监控配置环境变量
        if "monitoring" in config_data:
            monitor_config = config_data["monitoring"]
            if "alert" in monitor_config and "webhook" in monitor_config["alert"]:
                webhook_config = monitor_config["alert"]["webhook"]
                webhook_config["url"] = os.getenv("ALERT_WEBHOOK_URL", webhook_config.get("url", ""))
        
        return config_data
    
    @classmethod
    def create_default_config(cls, output_path: Optional[str] = None) -> str:
        """
        创建默认配置文件
        
        Args:
            output_path: 输出文件路径，如果为None则使用默认路径
            
        Returns:
            str: 创建的配置文件路径
        """
        if output_path is None:
            output_path = cls._get_default_config_path()
        
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        default_config = AppConfig()
        config_dict = default_config.model_dump(exclude_unset=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            yaml.dump(config_dict, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        
        return str(output_file)