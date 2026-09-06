-- 關閉 orgtd.applescript
-- 編譯：osacompile -o "orgtd/gui/關閉 orgtd.app" "orgtd/gui/關閉 orgtd.applescript"

property projectDir : "/Users/jerrychen/My_Projects/orgtd"
property runDir : "/Users/jerrychen/My_Projects/orgtd/run"
property pidFile : "/Users/jerrychen/My_Projects/orgtd/run/orgtd.pid"
property appTitle : "orgtd"

on run
	do shell script "mkdir -p " & quoted form of runDir

	set killedAny to false
	set failedPids to {}

	-- 1. 先處理 pid 檔記錄的行程
	set pidFileExists to (do shell script "test -f " & quoted form of pidFile & " && echo yes || echo no")
	if pidFileExists is "yes" then
		set pidListText to do shell script "cat " & quoted form of pidFile & " 2>/dev/null; true"
		repeat with onePid in paragraphs of pidListText
			set onePid to onePid as text
			if onePid is not "" then
				set r to my killPid(onePid)
				if r is "terminated" or r is "force_killed" then
					set killedAny to true
				else if r is "failed" then
					set end of failedPids to onePid
				end if
			end if
		end repeat
	end if

	-- 2. 保險：不管 pid 檔怎麼說，port 5001 上如果還有殘留行程一併清掉
	--    （處理 pid 檔遺失，或 reloader 產生但未被追蹤到的子行程）
	set leftoverCount to do shell script "lsof -ti :5001 2>/dev/null | wc -l | tr -d '[:space:]'"
	if leftoverCount is not "0" then
		set leftoverPids to do shell script "lsof -ti :5001 2>/dev/null"
		repeat with onePid in paragraphs of leftoverPids
			set onePid to onePid as text
			if onePid is not "" then
				set r to my killPid(onePid)
				if r is "terminated" or r is "force_killed" then
					set killedAny to true
				else if r is "failed" then
					set end of failedPids to onePid
				end if
			end if
		end repeat
	end if

	-- 3. 最終確認 port 是否真的空了
	set finalCount to do shell script "lsof -ti :5001 2>/dev/null | wc -l | tr -d '[:space:]'"

	if finalCount is not "0" then
		set remaining to do shell script "lsof -i :5001 -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print \"PID \" $2 \"（\" $1 \"）\"}'; true"
		display alert "orgtd 無法完全關閉" message "已嘗試關閉，但 port 5001 上仍有行程無法結束：" & return & return & remaining & return & return & "請手動檢查：lsof -i :5001，或用「活動監視器」強制結束對應行程。" as critical buttons {"好"} default button "好"
		return
	end if

	do shell script "rm -f " & quoted form of pidFile

	if killedAny then
		display notification "orgtd 已關閉，port 5001 已釋放。" with title appTitle sound name "Glass"
	else
		display dialog "orgtd 本來就沒有在執行，不需要關閉。" with title appTitle buttons {"好"} default button "好" with icon note
	end if
end run

-- 對單一 PID 做「先溫和(SIGTERM)、等 3 秒、還沒死再強制(SIGKILL)」
-- 回傳："no_such_process" | "terminated" | "force_killed" | "failed"
on killPid(targetPid)
	set isAlive to do shell script "kill -0 " & targetPid & " 2>/dev/null && echo yes || echo no"
	if isAlive is "no" then return "no_such_process"

	do shell script "kill -TERM " & targetPid & " 2>/dev/null; true"

	set stillAlive to true
	set waited to 0
	repeat until waited ≥ 6
		delay 0.5
		set waited to waited + 1
		set checkAlive to do shell script "kill -0 " & targetPid & " 2>/dev/null && echo yes || echo no"
		if checkAlive is "no" then
			set stillAlive to false
			exit repeat
		end if
	end repeat

	if not stillAlive then return "terminated"

	do shell script "kill -KILL " & targetPid & " 2>/dev/null; true"
	delay 0.3
	set finalAlive to do shell script "kill -0 " & targetPid & " 2>/dev/null && echo yes || echo no"
	if finalAlive is "no" then
		return "force_killed"
	else
		return "failed"
	end if
end killPid
