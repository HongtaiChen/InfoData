
with pre_trade_date as(
select
	a.*,
	last_day(a.trade_date) last_month_date,
	max(case when a.is_trading_day = 1 then a.trade_date else null end)over(order by a.trade_date ) pre_trade_date
from
	adata.trade_calendar a
 where  a.trade_date  >= '20250407'
  and a.trade_date  <= '20250701'
  
),
begin_close_table as(
select a.trade_date,a.pre_trade_date, b.close begin_close,b.concept_name,b.concept_code
 from pre_trade_date a left join ths_concept_market b  
  on a.pre_trade_date  = b.trade_date 
where a.trade_date = a.last_month_date
)
select a.trade_date end_date, b.trade_date begin_date, a.close end_close, b.begin_close, ((a.close-b.begin_close ) /b.begin_close)*100 as growth_rate,a.concept_name
  from ths_concept_market a left join begin_close_table b  
 on a.concept_code = b.concept_code  
   where a.trade_date = '20250801'
  order by b.trade_date,((a.close-b.begin_close ) /b.begin_close)*100  desc