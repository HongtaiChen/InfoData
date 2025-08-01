import adata


# k_type: k线类型：1.日；2.周；3.月 默认：1 日k
res_df = adata.stock.info.get_industry_sw(stock_code='603918')
print(res_df)



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