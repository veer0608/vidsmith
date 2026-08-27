# Why Your Backup Has Never Been Tested

## The comfortable assumption
[visual: server rack status lights]
You have backups running every night and a green tick in the dashboard to prove it. That tick means the job finished. It does not mean anything inside it can be read back.

## What actually fails
[visual: hands holding damaged hard drive]
Backups fail quietly. A path changes, a credential expires, a disk fills, and the job still reports success because it did everything it was asked to do. The gap between finished and usable is where the outage lives.

## The only test that counts
[visual: engineer restoring data on laptop]
Restore it. Pick a random file from last month, pull it out of the backup onto a clean machine, and open it. If that takes you longer than an hour, you do not have a backup. You have a hope.

## What to do on Monday
[visual: calendar with circled date]
Put one restore in the calendar every quarter and treat a failed restore as an incident. The first one will find something broken. That is the point.