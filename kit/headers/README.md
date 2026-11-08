# headers/

Lead's frozen headers go here (copy from the ACTIVE_SOURCE header folder). `match.py` puts this folder on INCLUDE.
Do not edit a header to make one function match: a header change re-compiles every matched function in the
nightly rebuild, and a change that moves bytes in any of them is rejected. Propose header changes as a separate
commit touching only `headers/`, with the VA that needs it in the commit message.
