#!/bin/sh
# Open the Zade app, replacing a window that is already open (so there is only ever one, running
# the newest code). Kept as a script because desktop launchers split quoted Exec lines differently.
dir=$(cd "$(dirname "$0")" && pwd)
pkill -f "^(/usr/bin/)?qs -p $dir/app.qml\$"
exec qs -p "$dir/app.qml"
