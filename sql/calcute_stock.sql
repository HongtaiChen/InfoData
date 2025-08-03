
-- 获取上一个交易日日历
with pre_trade_date as(
select
	a.*,
	last_day(a.trade_date) last_month_date,
	max(case when a.is_trading_day = 1 then a.trade_date else null end)over(order by a.trade_date ) pre_trade_date
from
	adata.trade_calendar a
 where a.trade_date >= '20250407'
  and a.trade_date <= '20250701'
  
),
-- 获取中报预增概念下股票列表
zbyz as(
select concat(b.stock_code , '.', b.exchange) stock_code,
	   b.short_name,
	   a.concept_name
  from ths_stock_concepts a
left join stock_info b  
on a.stock_code = b.stock_code 
where a.index_code in ('886104')
),
-- 获取AI概念下股票列表
ai as(
select concat(b.stock_code , '.', b.exchange) stock_code,
	   b.short_name
  from ths_stock_concepts a
left join stock_info b  
on a.stock_code = b.stock_code 
where a.index_code in ('886102', '886100', '886099')
group by b.stock_code,b.exchange,b.short_name
),
-- 获取既是中报预增，又是AI概念的股票期末行情数据
end_close_table as(
select c.*,
	   c.close end_close,
	   a.short_name,
	   a.concept_name
 from zbyz a inner join ai b 
 on a.stock_code = b.stock_code
 inner join stock_market_daily c   
 on a.stock_code = c.stock_code
where c.trade_date = '2025-07-31'
),

-- 获取既是中报预增，又是AI概念的股票期初行情数据
begin_close_table as(
select c.*,
	   c.close begin_close,
	   a.short_name,
	   a.concept_name
 from zbyz a inner join ai b 
 on a.stock_code = b.stock_code
 inner join stock_market_daily c   
 on a.stock_code = c.stock_code
where c.trade_date in (select pre_trade_date from pre_trade_date d where d.trade_date = d.last_month_date)
)
select
	a.stock_code,
	a.short_name,
	a.trade_date end_date,
	b.trade_date begin_date,
	a.end_close end_close,
	b.begin_close,
	((a.end_close-b.begin_close ) / b.begin_close)* 100 as growth_rate
from
	end_close_table a
left join begin_close_table b  
 on
	a.stock_code = b.stock_code
order by
	b.trade_date,
	((a.close-b.begin_close ) / b.begin_close)* 100 desc;