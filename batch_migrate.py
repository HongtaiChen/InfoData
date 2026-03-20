#!/usr/bin/env python3
"""
批量迁移脚本

一次性迁移所有剩余的脚本到新架构。
"""

import os
import sys
import re
import shutil
import logging
from datetime import datetime
from pathlib import Path

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('batch_migration.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class BatchMigrator:
    """批量迁移器"""
    
    def __init__(self, project_root: str):
        self.project_root = Path(project_root)
        self.migration_report = {
            "start_time": datetime.now(),
            "scripts_analyzed": [],
            "scripts_migrated": [],
            "scripts_skipped": [],
            "errors": []
        }
        
    def discover_scripts(self):
        """发现所有需要迁移的脚本"""
        scripts = []
        
        # 搜索Python文件
        for py_file in self.project_root.rglob("*.py"):
            # 排除某些目录
            if any(exclude in str(py_file) for exclude in ["__pycache__", ".git", "src/", "test_", "migrate_", "batch_"]):
                continue
            
            # 检查是否包含旧架构代码
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 检查是否包含旧架构模式
                if ('import akshare' in content or 
                    'import tushare' in content or 
                    'import pymysql' in content):
                    scripts.append(py_file)
                    
            except Exception as e:
                logger.warning(f"检查文件失败 {py_file}: {e}")
        
        # 排序：先迁移简单的，再迁移复杂的
        scripts.sort(key=lambda x: x.stat().st_size)
        
        return scripts
    
    def analyze_script(self, script_path: Path):
        """分析脚本"""
        try:
            with open(script_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            analysis = {
                "path": str(script_path),
                "size_kb": script_path.stat().st_size / 1024,
                "lines": len(content.splitlines()),
                "has_akshare": "import akshare" in content,
                "has_tushare": "import tushare" in content,
                "has_pymysql": "import pymysql" in content,
                "has_configparser": "import configparser" in content,
                "complexity": self._assess_complexity(content),
                "backup_created": False,
                "migrated": False,
                "new_path": None
            }
            
            return analysis
            
        except Exception as e:
            logger.error(f"分析脚本失败 {script_path}: {e}")
            return None
    
    def _assess_complexity(self, content: str) -> str:
        """评估脚本复杂度"""
        complexity = "low"
        
        # 检查并发
        if "ThreadPoolExecutor" in content or "concurrent.futures" in content:
            complexity = "high"
        # 检查多函数
        elif len(re.findall(r'def\s+\w+\(', content)) > 5:
            complexity = "medium"
        # 检查数据库操作
        elif len(re.findall(r'INSERT INTO|UPDATE|DELETE FROM', content, re.IGNORECASE)) > 3:
            complexity = "medium"
        
        return complexity
    
    def create_backup(self, script_path: Path):
        """创建备份"""
        backup_path = script_path.with_suffix(script_path.suffix + '.backup')
        try:
            shutil.copy2(script_path, backup_path)
            logger.info(f"创建备份: {backup_path}")
            return True
        except Exception as e:
            logger.error(f"创建备份失败 {script_path}: {e}")
            return False
    
    def migrate_script(self, script_path: Path, analysis: dict):
        """迁移单个脚本"""
        try:
            # 确定新文件名
            if script_path.name.startswith("daily_"):
                new_name = script_path.name.replace(".py", "_new.py")
            elif script_path.name.startswith("monthly_"):
                new_name = script_path.name.replace(".py", "_new.py")
            elif script_path.name.startswith("weekly_"):
                new_name = script_path.name.replace(".py", "_new.py")
            else:
                new_name = script_path.stem + "_new.py"
            
            new_path = script_path.parent / new_name
            
            # 读取原内容
            with open(script_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 基本迁移：替换导入语句
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
迁移版本: {script_path.name}
原文件: {script_path}
迁移时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
迁移状态: 基础导入替换完成

注意:
1. 需要手动替换函数调用
2. 需要添加错误处理
3. 需要测试功能完整性
"""

'''
            
            migrated_content = migration_header + migrated_content
            
            # 写入新文件
            with open(new_path, 'w', encoding='utf-8') as f:
                f.write(migrated_content)
            
            analysis["new_path"] = str(new_path)
            analysis["migrated"] = True
            
            logger.info(f"迁移完成: {script_path} -> {new_path}")
            return True
            
        except Exception as e:
            logger.error(f"迁移脚本失败 {script_path}: {e}")
            return False
    
    def generate_migration_template(self, script_path: Path, analysis: dict):
        """生成迁移模板（更完整的版本）"""
        try:
            template_name = script_path.stem + "_template.py"
            template_path = script_path.parent / template_name
            
            template_content = f'''#!/usr/bin/env python3
"""
{script_path.name} - 新架构完整迁移模板

基于新架构的完整迁移版本，包含：
1. 统一的数据采集客户端
2. 数据存储管理器
3. 数据模型验证
4. 完整的错误处理
5. 环境变量配置

迁移状态: 需要根据原脚本逻辑完善具体实现
"""

import os
import sys
import logging
from datetime import datetime

# 添加src目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# 导入新架构模块
from data_collection.factory import get_akshare_client, get_tushare_client
from data_storage.manager import get_storage_manager
from config.manager import get_config_manager

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('{script_path.stem}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class {script_path.stem.title().replace('_', '')}Manager:
    """{script_path.name} 管理器"""
    
    def __init__(self):
        self.config = None
        self.client = None
        self.storage = None
        
    def setup(self):
        """设置客户端和存储管理器"""
        try:
            # 获取配置
            env = os.getenv("INFODATA_APP_ENV", "development")
            self.config = get_config_manager(env=env)
            
            logger.info(f"使用配置环境: {{env}}")
            
            # 创建数据采集客户端
            # 根据原脚本选择客户端
            {'# 使用AKShare客户端' if analysis['has_akshare'] else '# 使用Tushare客户端'}
            {'self.client = get_akshare_client(client_id="' + script_path.stem + '")' if analysis['has_akshare'] else '# self.client = get_tushare_client(token=os.getenv("INFODATA_TUSHARE_TOKEN"))'}
            
            # 创建数据存储管理器
            self.storage = get_storage_manager(
                host=self.config.get("database.host", "localhost"),
                port=self.config.get("database.port", 3306),
                user=self.config.get("database.user", "root"),
                password=self.config.get("database.password", ""),
                database=self.config.get("database.name", "infodata")
            )
            
            logger.info("客户端和存储管理器设置完成")
            return True
            
        except Exception as e:
            logger.error(f"设置失败: {{e}}")
            return False
    
    def execute(self):
        """执行主要逻辑"""
        logger.info("开始执行 {script_path.name}")
        
        try:
            # TODO: 根据原脚本逻辑实现具体功能
            
            # 示例：获取数据
            # if analysis['has_akshare']:
            #     data = self.client.get_stock_spot()
            # elif analysis['has_tushare']:
            #     data = self.client.get_daily()
            
            # 示例：插入数据
            # if not data.empty:
            #     inserted = self.storage.bulk_insert_data(data)
            #     logger.info(f"插入数据完成: {{inserted}} 条")
            
            logger.info("{script_path.name} 执行完成")
            return True
            
        except Exception as e:
            logger.error(f"执行失败: {{e}}")
            return False
    
    def cleanup(self):
        """清理资源"""
        if self.storage:
            self.storage.close()
            logger.info("数据存储管理器已关闭")


def main():
    """主函数"""
    logger.info("启动 {script_path.name}")
    
    manager = {script_path.stem.title().replace('_', '')}Manager()
    
    try:
        if not manager.setup():
            logger.error("初始化失败，退出")
            return False
        
        success = manager.execute()
        
        if success:
            logger.info("{script_path.name} 执行成功")
            return True
        else:
            logger.warning("{script_path.name} 执行失败")
            return False
            
    except KeyboardInterrupt:
        logger.info("用户中断执行")
        return False
    except Exception as e:
        logger.error(f"执行失败: {{e}}")
        import traceback
        logger.error(traceback.format_exc())
        return False
    finally:
        manager.cleanup()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
'''
            
            with open(template_path, 'w', encoding='utf-8') as f:
                f.write(template_content)
            
            logger.info(f"生成迁移模板: {template_path}")
            return str(template_path)
            
        except Exception as e:
            logger.error(f"生成迁移模板失败 {script_path}: {e}")
            return None
    
    def run_migration(self):
        """运行批量迁移"""
        logger.info("开始批量迁移...")
        
        # 发现脚本
        scripts = self.discover_scripts()
        logger.info(f"发现 {len(scripts)} 个需要迁移的脚本")
        
        for script_path in scripts:
            logger.info(f"处理脚本: {script_path}")
            
            # 分析脚本
            analysis = self.analyze_script(script_path)
            if not analysis:
                self.migration_report["errors"].append(f"分析失败: {script_path}")
                continue
            
            self.migration_report["scripts_analyzed"].append(analysis)
            
            # 创建备份
            backup_created = self.create_backup(script_path)
            analysis["backup_created"] = backup_created
            
            # 根据复杂度选择迁移策略
            if analysis["complexity"] == "low":
                # 简单脚本：直接迁移
                migrated = self.migrate_script(script_path, analysis)
                if migrated:
                    self.migration_report["scripts_migrated"].append(analysis)
                else:
                    self.migration_report["scripts_skipped"].append(analysis)
            else:
                # 复杂脚本：生成迁移模板
                template_path = self.generate_migration_template(script_path, analysis)
                if template_path:
                    analysis["template_path"] = template_path
                    self.migration_report["scripts_migrated"].append(analysis)
                else:
                    self.migration_report["scripts_skipped"].append(analysis)
        
        # 完成迁移
        self.migration_report["end_time"] = datetime.now()
        
        # 生成报告
        self.generate_report()
        
        return self.migration_report
    
    def generate_report(self):
        """生成迁移报告"""
        report_path = self.project_root / "batch_migration_report.md"
        
        report = f"""# 批量迁移报告

## 迁移概览
- **开始时间**: {self.migration_report['start_time'].strftime('%Y-%m-%d %H:%M:%S')}
- **结束时间**: {self.migration_report['end_time'].strftime('%Y-%m-%d %H:%M:%S')}
- **总耗时**: {(self.migration_report['end_time'] - self.migration_report['start_time']).total_seconds():.1f} 秒
- **分析脚本数**: {len(self.migration_report['scripts_analyzed'])}
- **成功迁移数**: {len(self.migration_report['scripts_migrated'])}
- **跳过脚本数**: {len(self.migration_report['scripts_skipped'])}
- **错误数**: {len(self.migration_report['errors'])}

## 脚本详情

### 成功迁移的脚本 ({len(self.migration_report['scripts_migrated'])})
"""
        
        for analysis in self.migration_report["scripts_migrated"]:
            report += f"\n#### {analysis['path']}\n"
            report += f"- 大小: {analysis['size_kb']:.1f} KB\n"
            report += f"- 行数: {analysis['lines']}\n"
            report += f"- 复杂度: {analysis['complexity']}\n"
            report += f"- 包含: "
            if analysis['has_akshare']:
                report += "AKShare "
            if analysis['has_tushare']:
                report += "Tushare "
            if analysis['has_pymysql']:
                report += "pymysql "
            report += "\n"
            if analysis.get('new_path'):
                report += f"- 新文件: {analysis['new_path']}\n"
            if analysis.get('template_path'):
                report += f"- 迁移模板: {analysis['template_path']}\n"
        
        if self.migration_report["scripts_skipped"]:
            report += f"\n### 跳过的脚本 ({len(self.migration_report['scripts_skipped'])})\n"
            for analysis in self.migration_report["scripts_skipped"]:
                report += f"- {analysis['path']} (复杂度: {analysis['complexity']})\n"
        
        if self.migration_report["errors"]:
            report += f"\n### 错误列表 ({len(self.migration_report['errors'])})\n"
            for error in self.migration_report["errors"]:
                report += f"- {error}\n"
        
        report += f"""

## 下一步建议

### 立即行动
1. **测试迁移后的脚本**: 运行 `python script_name_new.py` 测试功能
2. **完善迁移模板**: 根据原脚本逻辑完善模板中的具体实现
3. **更新配置文件**: 确保环境变量正确设置

### 后续工作
1. **创建测试套件**: 为所有迁移后的脚本创建测试
2. **性能优化**: 根据实际运行情况优化性能
3. **文档更新**: 更新项目文档反映架构变化

## 迁移策略说明

### 简单脚本（复杂度 low）
- 直接替换导入语句
- 生成带迁移注释的新文件
- 保留原逻辑结构

### 复杂脚本（复杂度 medium/high）
- 生成完整的迁移模板
- 提供新架构的框架代码
- 需要手动完善具体实现

---

**迁移完成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        
        logger.info(f"迁移报告生成完成: {report_path}")
        return report_path


def main():
    """主函数"""
    project_root = os.path.dirname(os.path.abspath(__file__))
    migrator = BatchMigrator(project_root)
    
    report = migrator.run_migration()
    
    # 输出摘要
    print("\n" + "="*60)
    print("批量迁移完成摘要")
    print("="*60)
    print(f"分析脚本数: {len(report['scripts_analyzed'])}")
    print(f"成功迁移数: {len(report['scripts_migrated'])}")
    print(f"跳过脚本数: {len(report['scripts_skipped'])}")
    print(f"错误数: {len(report['errors'])}")
    print(f"总耗时: {(report['end_time'] - report['start_time']).total_seconds():.1f} 秒")
    print("="*60)
    
    if report['errors']:
        print("\n错误列表:")
        for error in report['errors']:
            print(f"  - {error}")
    
    print(f"\n详细报告请查看: batch_migration_report.md")


if __name__ == "__main__":
    main()