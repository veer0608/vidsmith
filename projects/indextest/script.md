# Why Database Indexes Slow Down Your Writes

## The Index Trap
[visual: hands typing rapidly]
You added a database index to make your queries run faster, but now your application feels sluggish. You thought indexes were pure upside. They are not.

## The Hidden Tax
[visual: a scale tipping heavily]
You believed that organizing your table helps the database work less overall. In reality, you gave the engine extra chores to finish before saving any data.

## Writing Twice
[diagram: a single incoming arrow splitting into two paths]
Every time you insert a new row, the database must write that record into your main table. Then it has to stop, find the correct spot in your index structure, and write a pointer there too. You just doubled the physical disk writes for a single save command.

## Splitting Leaves
[diagram: a node box branching into two smaller boxes]
Your index is a balanced tree of keys and addresses. When a new key lands between existing entries, the page fills up and splits in half. The engine shuffles neighboring blocks across the disk just to make room for your single incoming value.

## Cache Misses
[visual: spinning hard drive platter]
The working set grows larger with every new index you create. Soon the index no longer fits in memory, forcing the server to fetch missing blocks from slow spinning disks on every write.

## Production Pain
[visual: server racks blinking red]
During a Black Friday flash sale, your checkout service grinds to a halt because twenty concurrent threads are fighting over lock contention on the same index root nodes.

## Strategic Cleanup
[visual: someone deleting lines of code]
Audit your slow query logs, find the unused indexes, and drop every single one that does not back a frequent search.

## Less Is More
[visual: a clean green checkmark]
Every index you delete is a heavy tax you refuse to pay twice.
