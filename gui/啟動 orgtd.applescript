-- 啟動 orgtd.applescript
-- 編譯：osacompile -o "orgtd/gui/啟動 orgtd.app" "orgtd/gui/啟動 orgtd.applescript"

property projectDir : "/Users/jerrychen/My_Projects/orgtd"
property venvPython : "/Users/jerrychen/My_Projects/orgtd/.venv/bin/python"
property runDir : "/Users/jerrychen/My_Projects/orgtd/run"
property pidFile : "/Users/jerrychen/My_Projects/orgtd/run/orgtd.pid"
property logFile : "/Users/jerrychen/My_Projects/orgtd/run/server.log"
property serverURL : "http://127.0.0.1:5001/"
property appTitle : "orgtd"

on run
	do shell script "mkdir -p " & quoted form of runDir

	set checkResult to my checkExistingInstance()

	if checkResult starts with "ALREADY_RUNNING:" then
		set existingPid to my afterColon(checkResult)
		display dialog "orgtd 已經在執行中（PID " & existingPid & "），不需要重複啟動。" & return & return & "如需重新啟動，請先雙擊「關閉 orgtd」再重新啟動一次。" with title appTitle buttons {"好"} default button "好" with icon note
		return
	end if

	if checkResult starts with "ADOPTED:" then
		set adoptedPid to my afterColon(checkResult)
		display dialog "偵測到 orgtd 已經在執行中（PID " & adoptedPid & "，可能是手動用 Terminal 啟動的），已接手追蹤，不需要重複啟動。" with title appTitle buttons {"好"} default button "好" with icon note
		return
	end if

	if checkResult starts with "PORT_CONFLICT:" then
		set conflictInfo to my afterColon(checkResult)
		display alert "orgtd 啟動失敗" message "Port 5001 已經被別的程式占用（不是 orgtd 自己）：" & return & return & conflictInfo & return & return & "請先手動關閉該程式（或用「活動監視器」找到對應 PID 結束），再重新啟動 orgtd。" as critical buttons {"好"} default button "好"
		return
	end if

	-- checkResult = "CLEAR"：真的沒在跑，正式啟動
	do shell script "rm -f " & quoted form of logFile

	set startCmd to "(cd " & quoted form of projectDir & " && exec nohup env ORGTD_NO_RELOAD=1 " & quoted form of venvPython & " app.py > " & quoted form of logFile & " 2>&1 < /dev/null) & echo $! | tee " & quoted form of pidFile

	set newPid to do shell script startCmd

	set serverUp to false
	set serverError to false
	set processAlive to true
	set attempts to 0
	repeat until attempts ≥ 20
		delay 0.5
		set attempts to attempts + 1

		set aliveCheck to do shell script "kill -0 " & newPid & " 2>/dev/null && echo yes || echo no"
		if aliveCheck is "no" then
			set processAlive to false
			exit repeat
		end if

		set httpCode to do shell script "curl -s -o /dev/null -w '%{http_code}' --max-time 1 " & quoted form of serverURL & " 2>/dev/null || echo 000"
		-- 只有 2xx 才算真的啟動成功；5xx 代表 process 有在監聽但請求一進來就
		-- 出錯（最常見是連不上 PostgreSQL，Flask server 本身不受影響照樣能
		-- bind port，要等實際請求觸發查詢才會爆），不能算「已啟動」。
		if httpCode starts with "2" then
			set serverUp to true
			exit repeat
		else if httpCode starts with "5" then
			set serverError to true
			exit repeat
		end if
	end repeat

	if serverUp then
		display notification "orgtd 已啟動（PID " & newPid & "），可以開啟 http://127.0.0.1:5001 使用了。" with title appTitle sound name "Glass"
		return
	end if

	if not processAlive then
		do shell script "rm -f " & quoted form of pidFile
		set logTail to my safeReadLogTail()
		display alert "orgtd 啟動失敗" message "伺服器行程啟動後隨即結束，最常見的原因是 PostgreSQL 沒有啟動。" & return & return & "請確認：brew services start postgresql@17" & return & return & "錯誤訊息（log 最後幾行，完整內容見 " & logFile & "）：" & return & logTail as critical buttons {"好"} default button "好"
		return
	end if

	if serverError then
		do shell script "kill -TERM " & newPid & " 2>/dev/null; true"
		do shell script "rm -f " & quoted form of pidFile
		display alert "orgtd 啟動失敗" message "伺服器行程有啟動，但一收到請求就發生錯誤（HTTP 5xx），最常見的原因是 PostgreSQL 沒有啟動或連不上。已自動關閉這個行程。" & return & return & "請確認：brew services start postgresql@17" & return & return & "錯誤訊息（log 最後幾行，完整內容見 " & logFile & "）：" & return & my safeReadLogTail() as critical buttons {"好"} default button "好"
		return
	end if

	set logTail to my safeReadLogTail()
	display alert "orgtd 啟動逾時" message "伺服器行程仍在執行中（PID " & newPid & "），但等了 10 秒仍連不上 http://127.0.0.1:5001。" & return & return & "可能只是第一次啟動比較慢，請稍後再雙擊「開啟 orgtd 網頁」試試看；如果一直連不上，請雙擊「關閉 orgtd」後查看 log：" & return & logFile & return & return & logTail as critical buttons {"好"} default button "好"
end run

-- 回傳："CLEAR" | "ALREADY_RUNNING:<pid>" | "ADOPTED:<pid>" | "PORT_CONFLICT:<detail>"
on checkExistingInstance()
	set pidFileExists to (do shell script "test -f " & quoted form of pidFile & " && echo yes || echo no")

	if pidFileExists is "yes" then
		set oldPid to do shell script "head -n1 " & quoted form of pidFile & " 2>/dev/null | tr -d '[:space:]'"
		if oldPid is not "" then
			set isAlive to do shell script "kill -0 " & oldPid & " 2>/dev/null && echo yes || echo no"
			if isAlive is "yes" then
				set cmdLine to do shell script "ps -p " & oldPid & " -o command= 2>/dev/null; true"
				if cmdLine contains "app.py" then
					return "ALREADY_RUNNING:" & oldPid
				end if
			end if
		end if
		-- pid 檔存在但行程已死、或 PID 已被別的行程回收：視為過期，清掉
		do shell script "rm -f " & quoted form of pidFile
	end if

	-- 沒有有效 pid 檔：port 才是真相，直接查 5001 上有沒有東西在聽
	set portPidCount to do shell script "lsof -ti :5001 2>/dev/null | wc -l | tr -d '[:space:]'"
	if portPidCount is "0" then return "CLEAR"

	set firstPortPid to do shell script "lsof -ti :5001 2>/dev/null | head -n1"
	set portCmd to do shell script "ps -p " & firstPortPid & " -o command= 2>/dev/null; true"
	-- 指令列只會顯示啟動時打的字串（例如相對路徑的 "app.py"），不會包含專案
	-- 絕對路徑，所以不能用指令列字串比對是不是這個專案；改查行程實際的工作
	-- 目錄（cwd）跟專案路徑是否一致，這才是可靠的判斷依據。
	set portCwd to do shell script "lsof -a -p " & firstPortPid & " -d cwd -Fn 2>/dev/null | awk '/^n/{print substr($0,2)}'; true"

	if portCmd contains "app.py" and portCwd is projectDir then
		-- port 上是 orgtd 自己（例如使用者手動用 Terminal 啟動），接管追蹤
		do shell script "lsof -ti :5001 2>/dev/null > " & quoted form of pidFile
		return "ADOPTED:" & firstPortPid
	end if

	set conflictDetail to do shell script "lsof -i :5001 -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print \"PID \" $2 \"（\" $1 \"）\"}'; true"
	return "PORT_CONFLICT:" & conflictDetail
end checkExistingInstance

on safeReadLogTail()
	try
		return do shell script "tail -n 15 " & quoted form of logFile & " 2>/dev/null"
	on error
		return "（無法讀取 log 檔）"
	end try
end safeReadLogTail

on afterColon(t)
	set colonPos to offset of ":" in t
	return text (colonPos + 1) thru -1 of t
end afterColon
