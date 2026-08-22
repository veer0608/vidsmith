# Why Database Indexes Make Queries Slower

## The Slow Query Mystery
[visual: frustrated developer looking at monitor]
Your slow database query finally needs a fix. You decide to add an index to speed things up. But suddenly your application runs even slower than before.

## The Promise of Indexes
[visual: fast glowing digital data stream]
Indexes are supposed to be magic shortcuts for data. They work like the index in the back of a textbook. You look up a term and jump straight to the correct page.

## How B Trees Work
[visual: complex branching tree diagram graphic]
Most databases build these shortcuts using tree structures. The system navigates down through sorted nodes to find your row. This avoids scanning every single record in the table.

## The Write Tax
[visual: typing rapidly on mechanical keyboard]
Reading data gets faster with this setup. But every single write operation pays a heavy price. The database now has to update both the table and the tree.

## Maintenance Overhead
[visual: gears turning inside heavy machinery]
Insertions and deletions require constant structural adjustments behind the scenes. If you have many indexes, one small write triggers a cascade of background work.

## Cache Eviction Trouble
[visual: overflowing storage boxes in warehouse]
Indexes also consume valuable memory space in your server cache. When the index grows too large it pushes out important data pages. This forces expensive disk reads.

## Optimizer Confusion
[visual: confused person at crossroads sign]
The query planner tries to calculate the absolute best path for your data. Too many choices confuse the optimizer. It might pick the wrong index and ruin performance.

## The Final Takeaway
[visual: clean minimalist green checkmark]
Always measure performance before and after adding an index.
