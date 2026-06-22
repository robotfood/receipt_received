# TODO

## Manual Region Retry

When staged VLM extraction misses or misreads fields, let the user draw a rectangle over the receipt image and rerun extraction on that selected region.

Target workflow:

1. User opens a receipt review page.
2. User selects a missing or incorrect field group, such as line items, supplier, date, tax, or payment.
3. User drags a rectangle over the relevant image area with CSS/JavaScript.
4. App crops that selected region server-side.
5. App sends only the crop plus a focused prompt to the local VLM.
6. App previews the proposed field updates before applying them.
7. User accepts, edits, or discards the result.

This should be especially useful when automatic line-item chunks time out or when the model reads the wrong area of the receipt.
