# import adata
# import pprint
# # 886015
# df = adata.stock.market.get_market_concept_ths(index_code = '886015')
# #df = adata.stock.info.all_concept_code_ths()

# pprint.pprint(df)


# # df = adata.stock.market.get_market(
# #                 stock_code='603918', 
# #                 start_date='19900101',
# #                 end_date='20250727', 
# #                 k_type=2,
# #                 adjust_type=1
# #             )
# # df = adata.stock.market.get_market_min('603918')
# params = {"fields1": "f1,f2,f3,f4,f5,f6",
#                   "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
#                   "ut": "7eea3edcaed734bea9cbfc24409ed989",
#                   "klt": '1', "fqt": '1',
#                   "secid": f"603918",
#                   "beg": '19900101', "end": '20250727',
#                   "_": "1623766962675",
#                   }
#         # 2. 请求url
# url = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
# r = requests.request(method='get', url=url, params=params)
# https://push2his.eastmoney.com/api/qt/stock/kline/get?cb=jQuery35106383674083432352_1749794267698&secid=0.603918&ut=fa5fd1943c7b386f172d6893dbfba10b&fields1=f1%2Cf2%2Cf3%2Cf4%2Cf5%2Cf6&fields2=f51%2Cf52%2Cf53%2Cf54%2Cf55%2Cf56%2Cf57%2Cf58%2Cf59%2Cf60%2Cf61&klt=101&fqt=1&end=20500101&lmt=120&_=1749794267721

# print(r)
# import adata
# import redis
# import json
# import numpy as np

# # 连接本地 Redis（默认 host=127.0.0.1, port=6379, db=0）
# r = redis.Redis(
#     host='127.0.0.1',  # Redis 服务器地址
#     port=6379,         # 端口（默认 6379）
#     db=0,              # 数据库编号（默认 0）
#     decode_responses=True  # 自动解码字节为字符串（重要！否则返回 bytes 类型）
# )
# res_df = r.hkeys('use_proxy')
# res_array = np.array(res_df)
# print(res_array[1])

# # 设置代理,代理是全局设置,代理失效后可重新设置。参数:ip,proxy_url
# adata.proxy(is_proxy=True, ip='127.0.0.1:5020')
# res_df = adata.stock.info.all_code()
# print(res_df)

# import tushare as ts
# ts.set_token('d74c40bf7bb33a39e27a8e8f47d1d628b09560c652f9caf713dc9db0')
# pro = ts.pro_api()
# df = pro.daily(ts_code='002466.SZ', start_date='20250731', end_date='20250731')
# print(df)

# from pyecharts import options as opts
# from pyecharts.charts import Kline

# # 准备数据
# data = [
#     [2320.26, 2320.26, 2287.3, 2362.94],
#     [2300, 2291.3, 2288.26, 2308.38],
#     [2295.35, 2346.5, 2295.35, 2345.92],
#     [2347.22, 2358.98, 2337.35, 2363.8],
#     # ... more data
# ]

# # 配置 Kline 图
# kline = (
#     Kline()
#     .add_xaxis(xaxis_data=["2017-10-24", "2017-10-25", "2017-10-26", "2017-10-27"])
#     .add_yaxis(series_name="Kline", y_axis=data)
#     .set_global_opts(
#         xaxis_opts=opts.AxisOpts(is_scale=True),
#         yaxis_opts=opts.AxisOpts(is_scale=True),
#         title_opts=opts.TitleOpts(title="Kline 示例"),
#     )
# )

# # 渲染图表
# kline.render("kline_chart.html")

# from datetime import datetime, timedelta

# today = datetime.now().strftime('%Y-%m-%d')
# print(today)

# import akshare as ak
# import pandas as pd
# from pyecharts.charts import *
# from pyecharts import options as opts
 
# df = ak.stock_zh_a_hist(symbol="600036", start_date='20250101',end_date='20250916', adjust="qfq").iloc[:, :6]
# df.columns = ['date','open','close','high','low','volume',]
 
# # 把date作为日期索引
# df.index = pd.to_datetime(df.date)
# df.index=df.index.strftime('%Y%m%d')
# df=df.sort_index()
# df['sma']=df.close.rolling(5).mean()
# df['lma']=df.close.rolling(10).mean()
# df['lma20']=df.close.rolling(20).mean()
# df['lma30']=df.close.rolling(30).mean()
# df['lma60']=df.close.rolling(60).mean()
 
# kline = (
#     Kline(init_opts=opts.InitOpts(width="1200px",height="600px"))
#     .add_xaxis(xaxis_data=list(df.index)) #X轴数据
#     .add_yaxis(
#         series_name="klines", #序列名称
#         y_axis=df[["open","close","low","high"]].values.tolist(), #Y轴数据
#         itemstyle_opts=opts.ItemStyleOpts(color="#ec0000", color0="#00da3c"),
#         markpoint_opts=opts.MarkPointOpts(
#             data=[#添加标记符
#             opts.MarkPointItem(type_='max', name='最大值'),
#             opts.MarkPointItem(type_='min', name='最小值'), ],
#             #symbol='circle',
#             #symbol_size=[100,30]
#         ),
#     )
#     .set_global_opts(
#         title_opts=opts.TitleOpts(title="K线及均线",pos_left='45%'), #标题位置
#         legend_opts=opts.LegendOpts(pos_right="35%",pos_top="5%"), #图例位置
#         #legend_opts=opts.LegendOpts(is_show=True, pos_bottom=10, pos_left="center"),
#         datazoom_opts=[
#             opts.DataZoomOpts(
#                 is_show=False,
#                 type_="inside", #内部缩放
#                 xaxis_index=[0,1],  #可缩放的x轴坐标编号
#                 range_start=0, range_end=100, #初始显示范围
#             ),
#             opts.DataZoomOpts(
#                 is_show=True, #显示滑块
#                 type_="slider", #滑块缩放
#                 xaxis_index=[0,1],  #可缩放的x轴坐标编号
#                 pos_top="85%",
#                 range_start=0, range_end=100, #初始显示范围
#             ),
#         ],
#         yaxis_opts=opts.AxisOpts(
#             is_scale=True, #缩放时是否显示0值
#             splitarea_opts=opts.SplitAreaOpts( #分割显示设置
#                 is_show=True, areastyle_opts=opts.AreaStyleOpts(opacity=1) ),
#         ),
#         tooltip_opts=opts.TooltipOpts( #提示框配置
#             trigger="axis", #坐标轴触发提示
#             axis_pointer_type="cross", #鼠标变为十字准星
#             background_color="rgba(245, 245, 245, 0.8)", #背景颜色
#             border_width=1, border_color="#ccc", #提示框配置
#             textstyle_opts=opts.TextStyleOpts(color="#000"), #文字配置
#         ),
#         visualmap_opts=opts.VisualMapOpts( #视觉映射配置
#             is_show=False, dimension=2,
#             series_index=5, is_piecewise=True,
#             pieces=[ {"value": 1, "color": "#00da3c"}, {"value": -1, "color": "#ec0000"},             ],
#         ),
#         axispointer_opts=opts.AxisPointerOpts( #轴指示器配置
#             is_show=True,
#             link=[{"xAxisIndex": "all"}],
#             label=opts.LabelOpts(background_color="#777"), #显示标签设置
#         ),
#         brush_opts=opts.BrushOpts(
#             x_axis_index="all", #所有series
#             brush_link="all", #不同系列选中后联动
#             out_of_brush={"colorAlpha": 0.1}, #高亮显示程度
#             brush_type="lineX", #纵向选择
#         ),
#     )
# )
 
# #均线
# line=Line()
# line.add_xaxis( df.index.tolist() ) #X轴数据
# line.add_yaxis( 'MA5', #序列名称
#                 df.sma.round(2).tolist(), #Y轴数据
#                 is_smooth=True, #平滑曲线
#                 is_symbol_show=False #不显示折线的小圆圈
# )
# line.add_yaxis( 'MA10',df.lma.round(2).tolist(),is_smooth=True,is_symbol_show=False )
# line.add_yaxis( 'MA20',df.lma20.round(2).tolist(),is_smooth=True,is_symbol_show=False )
# line.add_yaxis( 'MA30',df.lma30.round(2).tolist(),is_smooth=True,is_symbol_show=False )
# line.add_yaxis( 'MA60',df.lma60.round(2).tolist(),is_smooth=True,is_symbol_show=False )
# line.set_series_opts(
#     label_opts=opts.LabelOpts(is_show=False), #是否显示数据标签
#     linestyle_opts=opts.LineStyleOpts(width=1), #线宽
# )
# line.set_global_opts(
#     datazoom_opts=[
#         opts.DataZoomOpts(
#             is_show=False,
#             type_="inside", #图内缩放调整
#             xaxis_index=[0,1],  #可缩放的x轴坐标编号
#             range_start=0, range_end=100, #初始显示范围
#         ),
#         opts.DataZoomOpts(
#             is_show=True, #是否显示滑块
#             type_="slider", #外部滑块缩放调整
#             xaxis_index=[0,1],  #可缩放的x轴坐标编号
#             pos_top="85%",
#             range_start=0, range_end=100, #初始显示范围
#         ),
#     ],
#     legend_opts=opts.LegendOpts(pos_right="20%",pos_top="5%"), #图例位置
#     tooltip_opts=opts.TooltipOpts(trigger="axis", axis_pointer_type="cross") #趋势线设置
# )
# kline.overlap(line)
 
# #成交量
# bar = (
#     Bar()
#     .add_xaxis(xaxis_data=list(df.index)) #X轴数据
#     .add_yaxis(
#         series_name="volume",
#         y_axis=df["volume"].tolist(), #Y轴数据
#         xaxis_index=1,
#         yaxis_index=1,
#         label_opts=opts.LabelOpts(is_show=False),
#         itemstyle_opts=opts.ItemStyleOpts(
#             color='#ef232a' #'#14b143'
#         ),
#     )
#     .set_global_opts(
#         xaxis_opts=opts.AxisOpts(
#             type_="category", #坐标轴类型-离散数据
#             grid_index=1,
#             axislabel_opts=opts.LabelOpts(is_show=False),
#         ),
#         legend_opts=opts.LegendOpts(is_show=False),
#     )
# )
 
# #图像排列
# grid_chart = Grid(
#     init_opts=opts.InitOpts(
#         width="1200px", #显示图形宽度
#         height="600px",
#         animation_opts=opts.AnimationOpts(animation=False), #关闭动画
#     )
# )
 
# grid_chart.add( #加入均线图
#     kline,
#     grid_opts=opts.GridOpts(pos_left="10%", pos_right="8%", height="40%"),
# )
# grid_chart.add( #加入成交量图
#     bar,
#     grid_opts=opts.GridOpts(pos_left="10%", pos_right="8%", pos_top="60%", height="20%"),
# )
# grid_chart.render("volume.html")

# from datetime import datetime, timedelta
# import time
# current_year = datetime.now().year
# print(current_year)



import akshare as ak

news_trade_notify_dividend_baidu_df = ak.news_trade_notify_dividend_baidu(date="19990726")
print(news_trade_notify_dividend_baidu_df)