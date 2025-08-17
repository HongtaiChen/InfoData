insert into stock_info_ex 
SELECT 
    id, 
    stock_code, 
    short_name, 
    null as type_name,  -- 这里可以用as给字段起别名（可选）
    exchange, 
    list_date, 
    sysdate() as update_time,  -- 注意sysdate()需要加括号
    '人工' as data_source 
FROM adata.stock_info a  
where a.stock_code not in (select b.stock_code  from stock_info_ex b );


--- 获取股票基本信息及股息率数据
select
	a.*,
	b.cumulative_dividends ,
	b.annual_average_dividend ,
	b.dividend_cnt,
	TIMESTAMPDIFF(YEAR, a.list_date, NOW()) as list_years
from
	stock_info_ex a
left join adata.stock_history_dividend b on
	a.stock_code = b.stock_code;


--- 获取股票基本信息及股息率数据-筛选过滤条件
select
	a.*,
	b.cumulative_dividends ,
	b.annual_average_dividend ,
	b.dividend_cnt,
	TIMESTAMPDIFF(YEAR, a.list_date, NOW()) as list_years
from
	stock_info_ex a
left join adata.stock_history_dividend b on
	a.stock_code = b.stock_code
 where abs(TIMESTAMPDIFF(YEAR, a.list_date, NOW()) - IFNULL(b.dividend_cnt , 0)) <= 5
  and IFNULL(b.annual_average_dividend  , 0) > 1.0