import pymysql
import pymysql.cursors
import os
import time
import socket
import re
import traceback
from typing import List, Dict
from volcenginesdkarkruntime import Ark
import json
from datetime import datetime, timedelta


# 数据库配置
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'root',
    'database': 'adata',
    'port': 3306,
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
    'connect_timeout': 10
}

# 豆包seed大模型配置 - 基于volcengine SDK
DOUBAO_CONFIG = {
    'model': 'doubao-seed-1-6-250615',  # 豆包seed模型
    'timeout': 60,  # SDK超时时间
    'retry_count': 3,  # 重试次数
    'retry_delay': 2  # 重试延迟(秒)
}


class FinanceConceptAnalyzer:
    def __init__(self):
        # 初始化方舟客户端
        self.ark_client = self._init_ark_client()
        # 初始化数据库连接
        self.db_conn = self._init_db_conn()
        self.cursor = self.db_conn.cursor() if self.db_conn else None
        # 加载概念数据
        self.concepts = self._load_concepts() if self.cursor else []
        
    def _init_ark_client(self) -> Ark:
        """初始化方舟客户端"""
        try:
            # 从环境变量获取API密钥
            api_key = "e472302f-4f5b-417c-989b-eadd94978578"
            if not api_key:
                raise ValueError("未设置ARK_API_KEY环境变量，请先配置")
            
            client = Ark(api_key=api_key)
            print("方舟客户端初始化成功")
            return client
        except Exception as e:
            print(f"方舟客户端初始化失败: {str(e)}")
            return None
    
    def _init_db_conn(self) -> pymysql.connections.Connection:
        """初始化数据库连接"""
        for attempt in range(3):
            try:
                conn = pymysql.connect(**DB_CONFIG)
                print("数据库连接成功")
                return conn
            except Exception as e:
                print(f"数据库连接尝试 {attempt+1}/3 失败: {str(e)}")
                if attempt < 2:
                    time.sleep(2)
        print("数据库连接失败，部分功能不可用")
        return None
        
        
    def _load_concepts(self) -> List[Dict]:
        print("加载同花顺概念信息...")
        if not self.cursor:
            print("数据库连接异常，无法加载概念信息")
            return []
        try:
            self.cursor.execute("SET max_execution_time = 10000")
            self.cursor.execute("SELECT concept_code, concept_name FROM ths_concept_info GROUP BY concept_code, concept_name")
            concepts = self.cursor.fetchall()
            print(f"共加载 {len(concepts)} 个概念")
            return concepts
        except Exception as e:
            print(f"加载概念信息失败: {str(e)}")
            return []
        
    def _call_doubao_api(self, prompt: str) -> Dict:
        """使用volcengine SDK调用豆包模型"""
        if not self.ark_client:
            print("方舟客户端未初始化，无法调用API")
            return None
        
        # 构造消息
        messages = [
            {"role": "system", "content": "你是一位拥有20年实战经验的顶级股票分析师，精通技术分析、基本面分析、市场心理学、量化交易和分析财经事件对同花顺概念板块整体的影响。擅长发现成长股、捕捉行业轮动机会，在牛熊市中都能保持稳定收益。你的风格是价值投资与技术择时相结合，注重风险控制。"},
            {"role": "user", "content": prompt}
        ]
        
        for attempt in range(DOUBAO_CONFIG['retry_count']):
            try:
                print(f"API调用尝试 {attempt+1}/{DOUBAO_CONFIG['retry_count']}")
                start_time = time.time()
                
                # 使用SDK调用API
                completion = self.ark_client.chat.completions.create(
                    model=DOUBAO_CONFIG["model"],
                    messages=messages,
                    timeout=DOUBAO_CONFIG['timeout']
                )
                
                # 计算耗时
                elapsed = (time.time() - start_time) * 1000
                print(f"API调用成功 (耗时: {elapsed:.2f}ms)")
                
                # 转换为字典格式返回
                return {
                    "choices": [{
                        "message": {
                            "content": completion.choices[0].message.content
                        }
                    }]
                }
                
            except Exception as e:
                print(f"API调用异常 (尝试 {attempt+1}): {str(e)}")
                traceback.print_exc()
                
            # 重试延迟
            if attempt < DOUBAO_CONFIG['retry_count'] - 1:
                time.sleep(DOUBAO_CONFIG['retry_delay'] * (attempt + 1))  # 递增延迟
        
        print("所有API调用尝试均失败")
        return None # type: ignore
    
    def _parse_analysis_result(self, result: Dict) -> List[Dict]:
        try:
            if not result or 'choices' not in result or len(result['choices']) == 0:
                return []
                
            content = result['choices'][0]['message']['content'].strip()
            print(f"解析结果: {content[:100]}...")
            
            # 处理可能的格式问题
            content = content.replace(',]', ']').replace(',}', '}')
            
            if content.startswith('{') or content.startswith('['):
                return json.loads(content)
            
            # 尝试提取结构化数据
            pattern = r'"concept_code":"([^"]+)","concept_name":"([^"]+)","relation_type":"([^"]+)","relation_degree":(\d+),"analysis":"([^"]+)"'
            matches = re.findall(pattern, content)
            if matches:
                return [
                    {
                        "concept_code": m[0],
                        "concept_name": m[1],
                        "relation_type": m[2],
                        "relation_degree": int(m[3]),
                        "analysis": m[4]
                    } for m in matches
                ]
                
            return []
        except Exception as e:
            print(f"解析结果出错: {str(e)}")
            return []
    
    def _generate_analysis_prompt(self, event: Dict, concepts: List[Dict]) -> str:
        """生成分析提示词"""
        concept_names = [f"{c['concept_name']}({c['concept_code']})" for c in concepts[:400]]
        
        prompt = f"""
        请分析以下财经事件与提供的同花顺概念列表中哪些概念相关，并自主搜索网上同花顺相关概念的内容，结合内容，判断是此财经事件对于相关的同花顺概念板块的影响，是利好还是利空，同时给出-10到10的影响程度评分（10分是最大正面影响程度，-10分是最大负面影响程度）：
        
        财经事件信息：
        日期：{event['event_date']}
        标题：{event['title']}
        内容：{event['content'][:500]}
        数据来源：{event['data_source'] or '未知'}
        
        概念列表：
        {', '.join(concept_names)}
        
        请按照以下JSON格式返回结果，确保格式正确：
        [
            {{
                "concept_code": "概念代码",
                "concept_name": "概念名称",
                "relation_type": "利好或利空",
                "relation_degree": -10-10的数字,
                "analysis": "简要分析依据(200字以内)"
            }}
        ]
        
        注意：
        1. 只返回相关的概念，不相关的不要返回
        2. 确保relation_degree是数字
        3. 如果没有相关概念，返回空数组
        """
        return re.sub(r'\s+', ' ', prompt).strip()
    
    def analyze_event(self, event: Dict) -> List[Dict]:
        if not event or 'id' not in event:
            print("无效的事件数据")
            return []
            
        print(f"分析事件: {event['id']} - {event['title'][:30]}")
        
        # 生成提示词
        prompt = self._generate_analysis_prompt(event, self.concepts)
        
        # 调用API
        result = self._call_doubao_api(prompt)
        if not result:
            return []
            
        # 解析结果
        analysis_results = self._parse_analysis_result(result)
        print(f"找到 {len(analysis_results)} 个相关概念")
        
        return analysis_results
    
    def save_analysis_results(self, event_date: int,title, content,results: List[Dict]):
        if not self.cursor or not results:
            return
            
        insert_sql = """
        INSERT INTO finance_concept_analysis 
        (event_date, title, content, concept_code, concept_name, relation_type, relation_degree, analysis, update_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE 
        relation_type = VALUES(relation_type),
        relation_degree = VALUES(relation_degree),
        analysis = VALUES(analysis),
        update_time = CURRENT_TIMESTAMP
        """
        
        try:
            data = [
                (
                    event_date,
                    title,
                    content,
                    r['concept_code'],
                    r['concept_name'],
                    r['relation_type'],
                    r['relation_degree'],
                    r['analysis'][:200],
                    datetime.now(),
                ) 
                for r in results
            ]
            
            self.cursor.executemany(insert_sql, data)
            self.db_conn.commit()
            print(f"已保存 {len(results)} 条分析结果")
        except Exception as e:
            print(f"保存结果失败: {str(e)}")
            self.db_conn.rollback()
    
    def process_events(self, begin_date ,end_date):
        if not self.cursor:
            print("数据库连接异常，无法处理事件")
            return
            
        query = """
        SELECT id, event_date, title, content, data_source 
        FROM finance_calendar 
        WHERE event_date >= %s 
          and event_date <= %s 
        ORDER BY event_date ASC
        """
        params = [begin_date ,end_date]
        
        # 删除当日数据
        self.cursor.execute(
            "DELETE from finance_concept_analysis WHERE event_date >= %s and event_date <= %s ",
            [begin_date ,end_date]
        )
        print(f"删除{begin_date}日到{end_date}分析结果，重新分析")
        
            
        try:
            self.cursor.execute(query, params)
            events = self.cursor.fetchall()
            print(f"共获取 {len(events)} 个事件需要分析")
            
            for i, event in enumerate(events):
                print(f"\n===== 处理事件 {i+1}/{len(events)} =====")
                
                # 分析事件
                results = self.analyze_event(event)
                
                # 保存结果
                if results:
                    self.save_analysis_results(event['event_date'], event['title'],event['content'],results)
                    
                # 控制API调用频率
                # time.sleep(0.05)
        except Exception as e:
            print(f"处理事件时出错: {str(e)}")
    
    def close(self):
        """关闭数据库连接"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.db_conn:
                self.db_conn.close()
            print("数据库连接已关闭")
        except Exception as e:
            print(f"关闭数据库连接出错: {str(e)}")

if __name__ == "__main__":

    
    analyzer = FinanceConceptAnalyzer()
    try:
        # 处理事件，可根据需要调整参数
        analyzer.process_events( begin_date= '20250918' , end_date='20260101')
    finally:
        analyzer.close()
