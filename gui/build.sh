#!/bin/sh
# 把三支 .applescript 編譯成雙擊可用的 .app。改了 .applescript 後重跑這支即可。
set -e
cd "$(dirname "$0")"

names="啟動 orgtd:com.jerrychen.orgtd.start
關閉 orgtd:com.jerrychen.orgtd.stop
開啟 orgtd 網頁:com.jerrychen.orgtd.open"

echo "$names" | while IFS=: read -r name bundle_id; do
  rm -rf "${name}.app"
  osacompile -o "${name}.app" "${name}.applescript"
  /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string ${bundle_id}" "${name}.app/Contents/Info.plist"
  codesign --force --sign - "${name}.app"
  echo "編譯完成：${name}.app"
done
