import adata
import sqlite3


# 连接sqlite数据库
conn = sqlite3.connect('stockinfo.db')

# 获取所有市场代码
res_df = adata.stock.info.all_code()
print(res_df)