select a.*, max(case when a.is_trading_day=1 then a.trade_date else null end)over(order by a.trade_date )  pre_trade_date from  adata.trade_calendar a;
