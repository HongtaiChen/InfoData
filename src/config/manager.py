"""
配置管理器

安全地管理应用程序配置，支持环境变量、配置文件和多环境。
"""

import os
import configparser
import json
from typing import Any, Dict, Optional, Union
from pathlib import Path
import logging


class ConfigError(Exception):
    """配置错误基类"""
    pass


class ConfigManager:
    """配置管理器
    
    安全地管理应用程序配置，支持环境变量、配置文件和多环境。
    """
    
    def __init__(
        self,
        env: str = "production",
        config_dir: Optional[Union[str, Path]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """初始化配置管理器
        
        Args:
            env: 环境名称（development, testing, production）
            config_dir: 配置文件目录
            logger: 日志记录器
        """
        self.env = env
        self.logger = logger or logging.getLogger(__name__)
        
        # 确定配置目录
        if config_dir:
            self.config_dir = Path(config_dir)
        else:
            self.config_dir = Path.cwd()
        
        # 配置存储
        self._config: Dict[str, Any] = {}
        self._sensitive_keys = set()
        
        # 加载配置
        self._load_all_configs()
    
    def _load_all_configs(self) -> None:
        """加载所有配置"""
        try:
            # 1. 加载默认配置
            self._load_default_config()
            
            # 2. 加载环境配置
            self._load_environment_config()
            
            # 3. 加载环境变量（覆盖文件配置）
            self._load_environment_variables()
            
            # 4. 加载敏感配置
            self._load_sensitive_config()
            
            self.logger.info(f"配置加载完成，环境: {self.env}")
            
        except Exception as e:
            self.logger.error(f"配置加载失败: {e}")
            raise ConfigError(f"配置加载失败: {e}") from e
    
    def _load_default_config(self) -> None:
        """加载默认配置"""
        default_config = {
            "app": {
                "name": "InfoData",
                "version": "1.0.0",
                "env": self.env,
                "debug": False,
                "log_level": "INFO",
            },
            "database": {
                "host": "localhost",
                "port": 3306,
                "name": "infodata",
                "user": "",
                "password": "",
                "charset": "utf8mb4",
                "pool_size": 10,
                "pool_recycle": 3600,
            },
            "data_sources": {
                "akshare_rate_limit_per_minute": 60,
                "akshare_rate_limit_per_hour": 1000,
                "tushare_rate_limit_per_minute": 60,
                "tushare_rate_limit_per_hour": 1000,
                "default_retries": 3,
                "default_retry_delay": 1.0,
            },
            "paths": {
                "data_dir": "./data",
                "log_dir": "./logs",
                "cache_dir": "./cache",
            },
            "security": {
                "encrypt_sensitive_data": True,
                "mask_logs": True,
                "validate_input": True,
            }
        }
        
        # 根据环境调整配置
        if self.env == "development":
            default_config["app"]["debug"] = True
            default_config["app"]["log_level"] = "DEBUG"
            default_config["database"]["name"] = "infodata_dev"
        
        elif self.env == "testing":
            default_config["app"]["debug"] = True
            default_config["app"]["log_level"] = "DEBUG"
            default_config["database"]["name"] = "infodata_test"
            default_config["database"]["host"] = "localhost"
        
        self._merge_config(default_config)
    
    def _load_environment_config(self) -> None:
        """加载环境特定配置文件"""
        config_files = [
            self.config_dir / "config.ini",
            self.config_dir / "config.json",
            self.config_dir / f"config.{self.env}.ini",
            self.config_dir / f"config.{self.env}.json",
        ]
        
        for config_file in config_files:
            if config_file.exists():
                self.logger.info(f"加载配置文件: {config_file}")
                self._load_config_file(config_file)
    
    def _load_config_file(self, file_path: Path) -> None:
        """加载配置文件
        
        Args:
            file_path: 配置文件路径
        """
        if file_path.suffix == ".ini":
            self._load_ini_file(file_path)
        elif file_path.suffix == ".json":
            self._load_json_file(file_path)
        else:
            self.logger.warning(f"不支持的配置文件格式: {file_path.suffix}")
    
    def _load_ini_file(self, file_path: Path) -> None:
        """加载INI配置文件
        
        Args:
            file_path: INI文件路径
        """
        parser = configparser.ConfigParser()
        parser.read(file_path, encoding='utf-8')
        
        config_dict = {}
        for section in parser.sections():
            config_dict[section] = dict(parser.items(section))
        
        self._merge_config(config_dict)
    
    def _load_json_file(self, file_path: Path) -> None:
        """加载JSON配置文件
        
        Args:
            file_path: JSON文件路径
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            config_dict = json.load(f)
        
        self._merge_config(config_dict)
    
    def _load_environment_variables(self) -> None:
        """加载环境变量"""
        env_prefix = "INFODATA_"
        
        for key, value in os.environ.items():
            if key.startswith(env_prefix):
                # 转换环境变量名为配置路径
                config_path = key[len(env_prefix):].lower()
                
                # 将下划线分隔的路径转换为嵌套字典
                parts = config_path.split('_')
                current = self._config
                
                # 遍历路径（除了最后一个部分）
                for part in parts[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
                
                # 设置值
                last_part = parts[-1]
                
                # 尝试解析JSON值
                try:
                    parsed_value = json.loads(value)
                    current[last_part] = parsed_value
                except (json.JSONDecodeError, ValueError):
                    # 如果不是JSON，使用原始字符串值
                    current[last_part] = value
                
                self.logger.debug(f"从环境变量加载配置: {config_path} = ****")
    
    def _load_sensitive_config(self) -> None:
        """加载敏感配置（从安全存储）"""
        # 敏感配置键列表
        sensitive_keys = [
            ("database", "password"),
            ("tushare", "token"),
            ("email", "password"),
            ("api_keys", ".*"),  # 所有API密钥
        ]
        
        # 标记敏感键
        for section, key_pattern in sensitive_keys:
            if section in self._config:
                if key_pattern == ".*":
                    # 标记该部分所有键为敏感
                    for key in self._config[section]:
                        self._sensitive_keys.add((section, key))
                elif key_pattern in self._config[section]:
                    self._sensitive_keys.add((section, key_pattern))
        
        # 尝试从安全存储加载敏感配置
        self._load_sensitive_from_vault()
    
    def _load_sensitive_from_vault(self) -> None:
        """从安全存储加载敏感配置"""
        # 这里可以实现从HashiCorp Vault、AWS Secrets Manager等加载
        # 目前使用环境变量
        
        # 数据库密码
        db_password = os.environ.get("INFODATA_DB_PASSWORD")
        if db_password:
            self._config.setdefault("database", {})["password"] = db_password
            self._sensitive_keys.add(("database", "password"))
            self.logger.info("从环境变量加载数据库密码")
        
        # Tushare Token
        tushare_token = os.environ.get("INFODATA_TUSHARE_TOKEN")
        if tushare_token:
            self._config.setdefault("tushare", {})["token"] = tushare_token
            self._sensitive_keys.add(("tushare", "token"))
            self.logger.info("从环境变量加载Tushare Token")
    
    def _merge_config(self, new_config: Dict[str, Any], parent_key: str = "") -> None:
        """合并配置字典
        
        Args:
            new_config: 新配置字典
            parent_key: 父键（用于递归）
        """
        for key, value in new_config.items():
            full_key = f"{parent_key}.{key}" if parent_key else key
            
            if isinstance(value, dict) and key in self._config and isinstance(self._config[key], dict):
                # 递归合并嵌套字典
                self._merge_config(value, key)
            else:
                # 直接设置值
                if key not in self._config:
                    self._config[key] = value
                    self.logger.debug(f"设置配置: {full_key} = {self._mask_value(full_key, value)}")
                else:
                    old_value = self._config[key]
                    self._config[key] = value
                    self.logger.debug(f"覆盖配置: {full_key} = {self._mask_value(full_key, value)} (原值: {self._mask_value(full_key, old_value)})")
    
    def _mask_value(self, key: str, value: Any) -> str:
        """掩码敏感值
        
        Args:
            key: 配置键
            value: 配置值
            
        Returns:
            掩码后的值字符串
        """
        if not isinstance(value, str):
            return str(value)
        
        # 检查是否是敏感键
        if "password" in key.lower() or "token" in key.lower() or "secret" in key.lower():
            return "****"
        
        return value
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值
        
        Args:
            key: 配置键，支持点号分隔（如"database.host"）
            default: 默认值
            
        Returns:
            配置值
        """
        try:
            parts = key.split('.')
            value = self._config
            
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    value = None
                
                if value is None:
                    break
            
            if value is None:
                self.logger.debug(f"配置键未找到: {key}，使用默认值: {self._mask_value(key, default)}")
                return default
            
            return value
            
        except Exception as e:
            self.logger.error(f"获取配置失败: {key} - {e}")
            return default
    
    def set(self, key: str, value: Any, sensitive: bool = False) -> None:
        """设置配置值
        
        Args:
            key: 配置键
            value: 配置值
            sensitive: 是否为敏感值
        """
        parts = key.split('.')
        current = self._config
        
        # 遍历到倒数第二部分
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            elif not isinstance(current[part], dict):
                current[part] = {}
            
            current = current[part]
        
        # 设置值
        last_part = parts[-1]
        current[last_part] = value
        
        # 标记敏感键
        if sensitive:
            section = parts[0] if len(parts) > 1 else ""
            self._sensitive_keys.add((section, last_part))
        
        self.logger.debug(f"设置配置: {key} = {self._mask_value(key, value)}")
    
    def get_all(self, mask_sensitive: bool = True) -> Dict[str, Any]:
        """获取所有配置
        
        Args:
            mask_sensitive: 是否掩码敏感值
            
        Returns:
            所有配置的字典
        """
        if not mask_sensitive:
            return self._config.copy()
        
        # 掩码敏感值
        def mask_dict(d, parent_key=""):
            masked = {}
            for key, value in d.items():
                full_key = f"{parent_key}.{key}" if parent_key else key
                
                if isinstance(value, dict):
                    masked[key] = mask_dict(value, full_key)
                else:
                    # 检查是否是敏感键
                    section = parent_key.split('.')[0] if parent_key else ""
                    if (section, key) in self._sensitive_keys:
                        masked[key] = "****"
                    else:
                        masked[key] = value
            
            return masked
        
        return mask_dict(self._config)
    
    def validate(self) -> bool:
        """验证配置
        
        Returns:
            配置是否有效
        """
        errors = []
        
        # 验证数据库配置
        db_host = self.get("database.host")
        db_name = self.get("database.name")
        
        if not db_host:
            errors.append("数据库主机未配置")
        
        if not db_name:
            errors.append("数据库名称未配置")
        
        # 验证必需路径
        required_paths = ["paths.data_dir", "paths.log_dir"]
        for path_key in required_paths:
            path_value = self.get(path_key)
            if not path_value:
                errors.append(f"路径配置未设置: {path_key}")
        
        if errors:
            self.logger.error(f"配置验证失败: {errors}")
            return False
        
        self.logger.info("配置验证成功")
        return True
    
    def setup_paths(self) -> None:
        """设置必需的目录路径"""
        paths_config = self.get("paths", {})
        
        for path_key in ["data_dir", "log_dir", "cache_dir"]:
            if path_key in paths_config:
                path = Path(paths_config[path_key])
                try:
                    path.mkdir(parents=True, exist_ok=True)
                    self.logger.debug(f"创建目录: {path}")
                except Exception as e:
                    self.logger.error(f"创建目录失败 {path}: {e}")


# 全局配置管理器实例
_config_manager: Optional[ConfigManager] = None


def get_config_manager(env: Optional[str] = None) -> ConfigManager:
    """获取全局配置管理器
    
    Args:
        env: 环境名称
        
    Returns:
        配置管理器实例
    """
    global _config_manager
    
    if _config_manager is None or (env and _config_manager.env != env):
        _config_manager = ConfigManager(env=env or "production")
    
    return _config_manager


def load_config(env: Optional[str] = None) -> ConfigManager:
    """加载配置（便捷函数）
    
    Args:
        env: 环境名称
        
    Returns:
        配置管理器实例
    """
    return get_config_manager(env)


def get_config(key: str, default: Any = None, env: Optional[str] = None) -> Any:
    """获取配置值（便捷函数）
    
    Args:
        key: 配置键
        default: 默认值
        env: 环境名称
        
    Returns:
        配置值
    """
    config = get_config_manager(env)
    return config.get(key, default)