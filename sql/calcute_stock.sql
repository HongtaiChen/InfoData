
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


----- 查出2024年所有股票的分红总金额-----------------
select a.stock_code, a.short_name ,sum(dividend_amount) dividend_amount
from (
select a.*,
        CAST(
        -- 数字部分转为DECIMAL，乘以单位对应的倍数
        (REGEXP_SUBSTR(a.dividend_amount_total , '^[0-9.]+') + 0)  -- 字符串转数值
        * CASE 
            WHEN dividend_amount_total LIKE '%亿' THEN 100000000  -- 1亿 = 10^8
            WHEN dividend_amount_total LIKE '%万' THEN 10000       -- 1万 = 10^4
            ELSE 1  -- 无单位则不乘（如纯数字）
        END 
    AS DECIMAL(20, 4)) 
     as dividend_amount 
     from adata.ths_stock_dividend a
where a.board_date  >= '2024-01-01'
  and a.board_date  <= '2024-12-31'
) a
group by a.stock_code, a.short_name 

-------------- 获取所有2024年有分红或者送股企业的年度总分红 ----------------
with dividends as(
select a.*,
	   SUBSTRING_INDEX(
    SUBSTRING_INDEX(dividend_plan_desc, '派', -1), -- 从右向左取“派”后的所有内容
    '元', 1 -- 从左向右取“元”前的内容
  ) as dividend_amount
from adata.ths_stock_dividend a 
where a.report_period = '2024年报'
  and a.dividend_plan_desc <> '不分配不转增'
),
changedate as(
select a.stock_code, max(change_date) change_date
  from adata.stock_shares a left join dividends b on a.stock_code = b.stock_code   
where a.change_date <= b.implementation_date 
 group by a.stock_code 
)
select
	a.stock_code,
	a.short_name,
	sum(dividend_amount) dividend_amount
from
	(
	select
		a.stock_code ,
		b.short_name ,
		a.total_shares * b. dividend_amount / 10 as dividend_amount
	from
		adata.stock_shares a
	join dividends b on
		a.stock_code = b.stock_code
	join changedate c on
		a.change_date = c.change_date
		and a.stock_code = c.stock_code 
) a
group by
	a.stock_code,
	a.short_name;


------ 获取所有股票的动态股息率 ----------------
with dividends as(
select a.*,
	   SUBSTRING_INDEX(
    SUBSTRING_INDEX(dividend_plan_desc, '派', -1),  -- 从右向左取“派”后的所有内容
    '元', 1                                   -- 从左向右取“元”前的内容
  ) AS dividend_amount
from adata.ths_stock_dividend a 
where a.report_period  = '2024年报'
  and a.dividend_plan_desc  <> '不分配不转增'
),
changedate as(
select a.stock_code, max(change_date) change_date
  from adata.stock_shares  a left join dividends b  on a.stock_code  = b.stock_code   
where a.change_date <= b.implementation_date 
 group by a.stock_code 
),
divdends_total as(
select a.stock_code,a.short_name, sum(dividend_amount) dividend_amount
 from (
select a.stock_code , b.short_name ,a.total_shares * b. dividend_amount/ 10 as dividend_amount
  from adata.stock_shares  a join dividends b  on a.stock_code  = b.stock_code   join changedate c on a.change_date  = c.change_date and a.stock_code  = c.stock_code 
) a
group by a.stock_code,a.short_name
),
price_changedate as(
select a.stock_code ,a.trade_date , a.`close` ,b.total_shares ,a.`close` * b.total_shares as total_price ,ROW_NUMBER() OVER ( PARTITION BY stock_code,trade_date  ORDER BY change_date desc ) AS rn
  from adata.stock_market_daily a left join adata.stock_shares  b  on a.stock_code  = b.stock_code and a.trade_date >= b.change_date    
where a.trade_date  >= '2025-07-01'
),
price_total as(
select  *
  from price_changedate a  
where a.rn = 1 
)
select a.*, b.*, b.dividend_amount / a.total_price   as gxl
  from price_total a left join divdends_total b on a.stock_code  = b.stock_code 
--where a.stock_code  = '002594'