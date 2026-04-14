# Invoice Discount

A tiny Python multi-file fixture for code-agent scenario tests.

Task target: add percentage discount support to invoice totals and summaries.

Expected agent task:

```text
请把这当成一个完整任务一次性完成：先解析这个 Python invoice 项目；然后增加折扣功能，让 total(items, discount_percent=10) 能返回打折后的总价，并让 invoice_summary(items, discount_percent=10) 返回折扣后的摘要；同时保持原有无折扣调用可用；修改后运行测试验证；最后总结改了什么和测试是否通过。
```

Initial state: `python -m unittest discover -s tests -v` fails until both the calculation layer and the summary layer support `discount_percent`.
