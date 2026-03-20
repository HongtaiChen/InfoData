"""
迁移工具

帮助将旧代码迁移到新架构的工具类。
"""

import os
import re
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime


class MigrationError(Exception):
    """迁移错误"""
    pass


class CodeMigrator:
    """代码迁移器
    
    分析旧代码并生成新架构的代码。
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        """初始化迁移器"""
        self.logger = logger or logging.getLogger(__name__)
        
        # 代码模式匹配
        self.patterns = {
            # AKShare导入
            "akshare_import": re.compile(r'^\s*import\s+akshare\s+as\s+ak', re.MULTILINE),
            "akshare_from_import": re.compile(r'^\s*from\s+akshare\s+import', re.MULTILINE),
            
            # Tushare导入
            "tushare_import": re.compile(r'^\s*import\s+tushare\s+as\s+ts', re.MULTILINE),
            "tushare_from_import": re.compile(r'^\s*from\s+tushare\s+import', re.MULTILINE),
            
            # pymysql导入
            "pymysql_import": re.compile(r'^\s*import\s+pymysql', re.MULTILINE),
            
            # AKShare函数调用
            "akshare_call": re.compile(r'ak\.([a-zA-Z_][a-zA-Z0-9_]*)', re.MULTILINE),
            
            # Tushare函数调用
            "tushare_call": re.compile(r'(?:pro\.|ts\.)([a-zA-Z_][a-zA-Z0-9_]*)', re.MULTILINE),
            
            # 数据库连接
            "db_connection": re.compile(r'pymysql\.connect\([^)]*\)', re.MULTILINE | re.DOTALL),
            
            # 硬编码配置
            "hardcoded_config": re.compile(r'host\s*=\s*[\'"][^\'"]*[\'"]', re.MULTILINE),
            "hardcoded_password": re.compile(r'password\s*=\s*[\'"][^\'"]*[\'"]', re.MULTILINE),
        }
        
        # 替换映射
        self.replacements = {
            # 导入替换
            "import akshare as ak": "from data_collection.factory import get_akshare_client",
            "import tushare as ts": "from data_collection.factory import get_tushare_client",
            "import pymysql": "from data_storage.manager import get_storage_manager",
            
            # 函数替换映射（AKShare）
            "ak.stock_zh_a_spot_em": "client.get_stock_spot()",
            "ak.stock_zh_a_hist": "client.get_stock_historical(symbol='{symbol}', start_date='{start}', end_date='{end}')",
            "ak.index_zh_a_hist": "client.get_index_historical(symbol='{symbol}', start_date='{start}', end_date='{end}')",
            "ak.stock_history_dividend": "client.get_stock_dividend_history(symbol='{symbol}')",
            "ak.fund_name_em": "client.get_fund_list()",
            "ak.bond_zh_us_rate": "client.get_bond_us_rate()",
            "ak.stock_jgdy_tj_em": "client.get_institutional_trading()",
            "ak.index_all_cni": "client.get_index_list()",
            "ak.futures_spot_price_previous": "client.get_futures_spot_price()",
            "ak.stock_fhps_detail_ths": "client.get_stock_dividend_detail(symbol='{symbol}')",
            "ak.fund_report_stock_cninfo": "client.get_fund_stock_report()",
            
            # 数据库操作替换
            "pymysql.connect(": "# 使用数据存储管理器\n    storage = get_storage_manager()",
            "cursor.execute(": "# 使用数据存储管理器执行查询\n    # storage.execute_query(",
            "conn.commit()": "# 数据存储管理器自动处理事务",
        }
    
    def analyze_file(self, file_path: str) -> Dict[str, Any]:
        """分析文件中的旧代码模式
        
        Args:
            file_path: 文件路径
            
        Returns:
            分析结果字典
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            analysis = {
                "file_path": file_path,
                "file_size": len(content),
                "lines": len(content.splitlines()),
                "patterns_found": {},
                "suggestions": [],
                "migration_complexity": "low"  # low, medium, high
            }
            
            # 检查各种模式
            for pattern_name, pattern in self.patterns.items():
                matches = pattern.findall(content)
                if matches:
                    analysis["patterns_found"][pattern_name] = {
                        "count": len(matches),
                        "examples": matches[:3]  # 只显示前3个例子
                    }
            
            # 根据找到的模式评估迁移复杂度
            found_patterns = set(analysis["patterns_found"].keys())
            
            # 检查复杂度
            if "db_connection" in found_patterns and "akshare_call" in found_patterns:
                analysis["migration_complexity"] = "high"
            elif "db_connection" in found_patterns or "akshare_call" in found_patterns:
                analysis["migration_complexity"] = "medium"
            
            # 生成建议
            suggestions = []
            
            if "akshare_import" in found_patterns:
                suggestions.append("替换AKShare导入为数据采集客户端")
            
            if "tushare_import" in found_patterns:
                suggestions.append("替换Tushare导入为数据采集客户端")
            
            if "pymysql_import" in found_patterns:
                suggestions.append("替换pymysql导入为数据存储管理器")
            
            if "hardcoded_password" in found_patterns:
                suggestions.append("移除硬编码密码，使用环境变量")
            
            if "db_connection" in found_patterns:
                suggestions.append("替换直接数据库操作为数据存储管理器")
            
            analysis["suggestions"] = suggestions
            
            return analysis
            
        except Exception as e:
            self.logger.error(f"分析文件失败 {file_path}: {e}")
            raise MigrationError(f"分析文件失败 {file_path}: {e}")
    
    def generate_migration_plan(self, file_path: str) -> Dict[str, Any]:
        """生成迁移计划
        
        Args:
            file_path: 文件路径
            
        Returns:
            迁移计划字典
        """
        analysis = self.analyze_file(file_path)
        
        plan = {
            "file_path": file_path,
            "analysis": analysis,
            "steps": [],
            "estimated_time": "15分钟",  # 根据复杂度调整
            "risks": [],
            "backup_required": True
        }
        
        # 根据复杂度调整预估时间
        if analysis["migration_complexity"] == "high":
            plan["estimated_time"] = "30-45分钟"
            plan["risks"].append("复杂的数据流转换")
        elif analysis["migration_complexity"] == "medium":
            plan["estimated_time"] = "20-30分钟"
        else:
            plan["estimated_time"] = "10-15分钟"
        
        # 生成步骤
        steps = []
        
        # 步骤1: 备份原文件
        steps.append({
            "step": 1,
            "action": "备份原文件",
            "description": f"创建备份文件 {file_path}.backup",
            "command": f"cp {file_path} {file_path}.backup"
        })
        
        # 步骤2: 更新导入语句
        if "akshare_import" in analysis["patterns_found"]:
            steps.append({
                "step": 2,
                "action": "更新AKShare导入",
                "description": "替换为数据采集客户端导入",
                "old_code": "import akshare as ak",
                "new_code": "from data_collection.factory import get_akshare_client"
            })
        
        if "tushare_import" in analysis["patterns_found"]:
            steps.append({
                "step": 3,
                "action": "更新Tushare导入",
                "description": "替换为数据采集客户端导入",
                "old_code": "import tushare as ts",
                "new_code": "from data_collection.factory import get_tushare_client"
            })
        
        if "pymysql_import" in analysis["patterns_found"]:
            steps.append({
                "step": 4,
                "action": "更新数据库导入",
                "description": "替换为数据存储管理器导入",
                "old_code": "import pymysql",
                "new_code": "from data_storage.manager import get_storage_manager"
            })
        
        # 步骤3: 替换函数调用
        if "akshare_call" in analysis["patterns_found"]:
            steps.append({
                "step": 5,
                "action": "替换AKShare函数调用",
                "description": "使用数据采集客户端方法",
                "details": "需要分析具体的函数调用并替换"
            })
        
        # 步骤4: 替换数据库操作
        if "db_connection" in analysis["patterns_found"]:
            steps.append({
                "step": 6,
                "action": "替换数据库操作",
                "description": "使用数据存储管理器",
                "details": "替换pymysql.connect()和相关操作"
            })
        
        # 步骤5: 添加错误处理
        steps.append({
            "step": len(steps) + 1,
            "action": "添加错误处理",
            "description": "添加try-except块和数据验证",
            "details": "包装关键操作在try-except中"
        })
        
        # 步骤6: 测试迁移
        steps.append({
            "step": len(steps) + 1,
            "action": "测试迁移",
            "description": "运行测试验证功能",
            "command": f"python {file_path} --test"
        })
        
        plan["steps"] = steps
        
        return plan
    
    def create_migrated_file(self, file_path: str, output_path: Optional[str] = None) -> str:
        """创建迁移后的文件（基础版本）
        
        Args:
            file_path: 源文件路径
            output_path: 输出文件路径（默认：原文件名.migrated.py）
            
        Returns:
            输出文件路径
        """
        if output_path is None:
            output_path = f"{file_path}.migrated.py"
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 基本替换
            migrated_content = content
            
            # 替换导入语句
            migrated_content = migrated_content.replace(
                "import akshare as ak",
                "# 迁移: 使用数据采集客户端\nfrom data_collection.factory import get_akshare_client"
            )
            
            migrated_content = migrated_content.replace(
                "import tushare as ts",
                "# 迁移: 使用数据采集客户端\nfrom data_collection.factory import get_tushare_client"
            )
            
            migrated_content = migrated_content.replace(
                "import pymysql",
                "# 迁移: 使用数据存储管理器\nfrom data_storage.manager import get_storage_manager"
            )
            
            # 添加迁移注释头
            migration_header = f'''"""
迁移版本: {os.path.basename(file_path)}
原文件: {file_path}
迁移时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
迁移状态: 基础导入替换完成

注意:
1. 需要手动替换函数调用
2. 需要添加错误处理
3. 需要测试功能完整性
"""

'''
            
            migrated_content = migration_header + migrated_content
            
            # 写入输出文件
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(migrated_content)
            
            self.logger.info(f"创建迁移文件: {output_path}")
            return output_path
            
        except Exception as e:
            self.logger.error(f"创建迁移文件失败 {file_path}: {e}")
            raise MigrationError(f"创建迁移文件失败 {file_path}: {e}")


class MigrationManager:
    """迁移管理器
    
    管理整个项目的迁移过程。
    """
    
    def __init__(self, project_root: str, logger: Optional[logging.Logger] = None):
        """初始化迁移管理器
        
        Args:
            project_root: 项目根目录
            logger: 日志记录器
        """
        self.project_root = project_root
        self.logger = logger or logging.getLogger(__name__)
        self.migrator = CodeMigrator(logger)
        
        # 迁移状态
        self.migration_status = {}
    
    def discover_files_to_migrate(self) -> List[str]:
        """发现需要迁移的文件
        
        Returns:
            需要迁移的文件列表
        """
        files_to_migrate = []
        
        # 搜索Python文件
        for root, dirs, files in os.walk(self.project_root):
            # 排除某些目录
            if 'src' in dirs:
                dirs.remove('src')  # 不遍历新架构代码
            if '.git' in dirs:
                dirs.remove('.git')
            
            for file in files:
                if file.endswith('.py'):
                    file_path = os.path.join(root, file)
                    
                    # 检查文件是否包含旧架构代码
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        
                        # 检查是否包含旧架构模式
                        if ('import akshare' in content or 
                            'import tushare' in content or 
                            'import pymysql' in content):
                            files_to_migrate.append(file_path)
                            
                    except Exception as e:
                        self.logger.warning(f"检查文件失败 {file_path}: {e}")
        
        return files_to_migrate
    
    def analyze_project(self) -> Dict[str, Any]:
        """分析整个项目
        
        Returns:
            项目分析结果
        """
        files_to_migrate = self.discover_files_to_migrate()
        
        analysis = {
            "total_files": len(files_to_migrate),
            "files": [],
            "summary": {
                "akshare_usage": 0,
                "tushare_usage": 0,
                "database_usage": 0,
                "hardcoded_passwords": 0
            }
        }
        
        for file_path in files_to_migrate:
            try:
                file_analysis = self.migrator.analyze_file(file_path)
                analysis["files"].append(file_analysis)
                
                # 更新摘要
                patterns = file_analysis["patterns_found"]
                if "akshare_import" in patterns or "akshare_call" in patterns:
                    analysis["summary"]["akshare_usage"] += 1
                if "tushare_import" in patterns or "tushare_call" in patterns:
                    analysis["summary"]["tushare_usage"] += 1
                if "pymysql_import" in patterns or "db_connection" in patterns:
                    analysis["summary"]["database_usage"] += 1
                if "hardcoded_password" in patterns:
                    analysis["summary"]["hardcoded_passwords"] += 1
                    
            except Exception as e:
                self.logger.error(f"分析文件失败 {file_path}: {e}")
        
        return analysis
    
    def generate_migration_report(self) -> str:
        """生成迁移报告
        
        Returns:
            迁移报告内容
        """
        analysis = self.analyze_project()
        
        report = f"""# InfoData 项目迁移报告

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
项目根目录: {self.project_root}

## 摘要
- 需要迁移的文件总数: {analysis['total_files']}
- 使用AKShare的文件: {analysis['summary']['akshare_usage']}
- 使用Tushare的文件: {analysis['summary']['tushare_usage']}
- 使用直接数据库操作的文件: {analysis['summary']['database_usage']}
- 包含硬编码密码的文件: {analysis['summary']['hardcoded_passwords']}

## 文件详情
"""
        
        for file_analysis in analysis["files"]:
            file_path = file_analysis["file_path"]
            relative_path = os.path.relpath(file_path, self.project_root)
            complexity = file_analysis["migration_complexity"]
            
            report += f"\n### {relative_path}\n"
            report += f"- 复杂度: {complexity}\n"
            report += f"- 行数: {file_analysis['lines']}\n"
            
            if file_analysis["patterns_found"]:
                report += "- 发现的模式:\n"
                for pattern_name, pattern_info in file_analysis["patterns_found"].items():
                    report += f"  - {pattern_name}: {pattern_info['count']} 处\n"
            
            if file_analysis["suggestions"]:
                report += "- 迁移建议:\n"
                for suggestion in file_analysis["suggestions"]:
                    report += f"  - {suggestion}\n"
        
        report += f"""

## 迁移建议

### 优先级排序
1. **高优先级**: 包含硬编码密码的文件
2. **中优先级**: 复杂度为high的文件
3. **低优先级**: 复杂度为low的文件

### 推荐迁移顺序
1. 先迁移简单的文件（复杂度low）
2. 再迁移中等复杂度的文件
3. 最后迁移复杂的文件

### 注意事项
1.