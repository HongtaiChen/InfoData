from pyecharts.charts import Line
from pyecharts import options as opts
import akshare as ak
import pandas as pd
import os

# 选取4个股票（不复权），放在同一个坐标轴里面展示。

# 股票列表
stock_codes = ["000001","600489","600028","601988"]

# 定义日期范围
start_date = '2023-01-01' # 这样定义无效，必须手动收入
end_date = '2023-01-10' # 这样定义无效，必须手动收入

road = []
# 遍历股票代码，并获取历史数据
for stock_code in stock_codes:
    # 使用 akshare 获取股票历史数据
    stock_data = ak.stock_zh_a_hist(symbol=stock_code,  period="daily", start_date="20200101", end_date='20230515', adjust="")
    
    # 日期是object格式，转化成日期格式
    stock_data['日期'] = pd.to_datetime( stock_data ['日期'] )
    
    # 用日期做索引，避免因停牌等情况发生时，导致合并后的数据存在缺失。
    stock_data.set_index( '日期' , inplace=True )
    
    # 重新定义列名，不重新定义的话就写成 多级列名切片
    stock_data.columns = stock_data.columns+stock_code
    road.append(stock_data)
# 多级列合并就用这个
# multiple_stock_data = pd.concat(road,axis=1,keys=stock_codes)

# 多数据合并
multiple_stock_data = pd.concat ( road , axis =1 )

# 把需要股票数据提取出来，看开盘价就写开盘，具体维度上期有图。
list_stock_K = [ '收盘' + x for x in stock_codes ]
df = multiple_stock_data[list_stock_K]

# 当前股票数据
akspot = ak.stock_zh_a_spot_em()
akspot.head(2)

# 使用 isin 函数进行筛选
# 重新定义stock_codes
filtered_df = akspot[akspot['代码'].isin( stock_codes )]
stock_codes = filtered_df['名称'].tolist()
# 股票代码转化成股票名称
df.columns = stock_codes

# 创建 Line 实例
line = Line()

# 添加 x 轴数据（日期）
line.add_xaxis(df.index.strftime('%Y-%m-%d').tolist()) # type: ignore

# 添加多个股票的 y 轴数据
# line.add_yaxis( stock_codes[0], df[list_stock_K[0]].tolist() ) 
# line.add_yaxis( stock_codes[1], df[list_stock_K[1]].tolist() )

# 循环添加股票的数据
for i in range( len( stock_codes ) ):
    line.add_yaxis(stock_codes[i] , df[stock_codes[i]].tolist())
    print(stock_codes[i])
    print(df[stock_codes[i]].tolist())

# 设置图表标题、坐标轴标签等
line.set_global_opts(
    title_opts=opts.TitleOpts(title='Stock Prices'),
    xaxis_opts=opts.AxisOpts(name='Date'),
    yaxis_opts=opts.AxisOpts(name='Price')
)


# 渲染生成 HTML 文件（可选）
# line.render_notebook()

# current_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
# html_path = os.path.join(current_dir, 'stock_price_comparison.html')
# line.render(html_path)