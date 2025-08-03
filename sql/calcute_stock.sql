
-------------获取对应概念下的股票涨跌幅
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


----------------------获取股票当前总市值-------------------
with max_change_date as
(
select
	a.stock_code ,
	max(change_date) change_date
from
	adata.stock_shares a
group by
	a.stock_code 
)

select
	a.short_name ,
	b.`close` * c.total_shares / 100000000 asset
from
	adata.stock_info a
inner join adata.stock_market_daily b  
on
	CONCAT(a.stock_code, '.', a.exchange ) = b.stock_code
inner join adata.stock_shares c  
on
	a.stock_code = c.stock_code
inner join max_change_date d  
on
	c.stock_code = d.stock_code
	and c.change_date = d.change_date
where
	b.trade_date = '20250801'


-- 获取股票今年以来涨跌幅 -----
with pre_trade_date as(
select
	a.*,
	last_day(a.trade_date) last_month_date,
	max(case when a.is_trading_day = 1 then a.trade_date else null end)over(order by a.trade_date ) pre_trade_date
from
	adata.trade_calendar a
 where a.trade_date >= '20240101'
  
),
 max_change_date as
(
select
	a.stock_code ,
	max(change_date) change_date
from
	adata.stock_shares a
group by
	a.stock_code 
),
total_asset as(
select
	a.short_name ,
	a.stock_code,
	CONCAT(a.stock_code, '.', a.exchange) concat_stock_code,
	b.`close` * c.total_shares / 100000000 asset,
	a.list_date
from
	adata.stock_info a
inner join adata.stock_market_daily b  
on
	CONCAT(a.stock_code, '.', a.exchange ) = b.stock_code
inner join adata.stock_shares c  
on
	a.stock_code = c.stock_code
inner join max_change_date d  
on
	c.stock_code = d.stock_code
	and c.change_date = d.change_date
where
	b.trade_date = '20250730'
)
select
	a.short_name ,
	a.stock_code,
	a.asset,
	a.list_date,
	(EXP(SUM(LOG(1+ change_pct/100))) -1 )*100 growthrate
from
	total_asset a
inner join adata.stock_market_daily b  
on
	a.concat_stock_code = b.stock_code
inner join adata.stock_shares c  
on
	a.stock_code = c.stock_code
inner join max_change_date d  
on
	c.stock_code = d.stock_code
	and c.change_date = d.change_date
where
	b.trade_date between  '20250101' and '20250801'
group by a.short_name ,
	a.stock_code,
	a.asset,
	a.list_date
