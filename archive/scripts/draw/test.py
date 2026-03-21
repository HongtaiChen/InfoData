from pyecharts.charts import Line
from pyecharts import options as opts
import akshare as ak
import pandas as pd
import pymysql
import logging
import sys
import json
import os
from datetime import datetime, timedelta
import time
import configparser
import traceback

config_file = 'D:\\Project\\ADATA\\adata\\daily_update_stock_info_config.ini'
config = configparser.ConfigParser()
config.read(config_file, encoding='utf-8')
db_config = {
            'host': config.get('database', 'host'),
            'port': config.getint('database', 'port'),
            'user': config.get('database', 'user'),
            'password': config.get('database', 'password'),
            'database': config.get('database', 'database'),
            'charset': config.get('database', 'charset')
        }

# 配置日志
def setup_logging():
    #todo:
    log_file = f"draw_chart_{datetime.now().strftime('%Y%m%d')}.log"  
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)

logger = setup_logging()



if __name__ == "__main__":
    connection = pymysql.connect(**db_config)
    try:
        # 用pandas读取MySQL数据为DataFrame
        sql = "select index_name, trade_date,close from dc_index_market a  where a.trade_date >= '20250407' and a.index_name in ('上证指数','沪深300','创业板','北证50','科创50','中证1000') order by a.trade_date;"
        df = pd.read_sql(sql, connection)
        # 1. 获取去重的日期列表（用于X轴）
        trade_dates = df.drop_duplicates(subset=['trade_date'], keep='first')['trade_date'].sort_values()
        # 转换为字符串格式，避免日期格式问题
        x_axis_dates = trade_dates.astype(str).tolist()
        logger.info(f"获取有效交易日 {len(x_axis_dates)} 天")
        # 2. 获取去重的指数名称列表
        index_names = df.drop_duplicates(subset=['index_name'], keep='first')['index_name'].tolist()
        logger.info(f"获取指数列表: {index_names}")
        # 3. 创建 Line 实例
        line = Line()
        line.add_xaxis(trade_dates.astype(str).tolist()) # type: ignore
        for name in index_names:
            # 筛选当前指数的数据
            index_data = df[df['index_name'] == name]
            # 按日期排序并提取收盘价（假设收盘价字段为close_price，根据实际表结构调整）
            index_close = index_data.sort_values('trade_date')['close']
            index_close = (index_close / index_close.iloc[0] - 1).tolist()
            line.add_yaxis(name, index_close,label_opts=opts.LabelOpts(is_show=False))
        # 设置图表标题、坐标轴标签等
        line.set_global_opts(
            # title_opts=opts.TitleOpts(title='Stock Prices'),
            xaxis_opts=opts.AxisOpts(name='Date'),
            yaxis_opts=opts.AxisOpts(name='Growth')
        )

        # 渲染生成 HTML 文件（可选）
        # line.render_notebook()

        current_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
        html_path = os.path.join(current_dir, 'stock_price_comparison.html')
        line.render(html_path)


    finally:
        connection.close()    