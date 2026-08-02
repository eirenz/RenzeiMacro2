; ============================================================================
; mouse_mode.ahk — 2D/3D Mouse-Mode State Machine
; ============================================================================
; Automatically detects when Roblox enters camera-rotation (3D) mode by
; checking RMB state + OS-level cursor-lock/visibility signals via Windows
; API. Switches mouse movement injection between absolute (2D) and relative
; delta (3D) accordingly.
;
; State Machine:
;   IDLE_2D → (RMB down) → RMB_DOWN_PENDING
;   RMB_DOWN_PENDING → (cursor lock confirmed) → MODE_3D
;   RMB_DOWN_PENDING → (RMB up, no lock) → IDLE_2D
;   MODE_3D → (RMB up OR cursor unlock) → IDLE_2D
;
; Also handles scroll-zoom-triggered first-person (no RMB held):
;   IDLE_2D → (cursor lock detected without RMB) → MODE_3D
;   MODE_3D → (cursor unlock) → IDLE_2D
; ============================================================================

#Requires AutoHotkey v2.0

class MouseMode {
    ; State constants
    static STATE_IDLE_2D := "IDLE_2D"
    static STATE_RMB_PENDING := "RMB_DOWN_PENDING"
    static STATE_MODE_3D := "MODE_3D"
    
    ; Current state
    currentState := "IDLE_2D"
    previousState := ""
    
    ; Polling interval for cursor-lock check (ms)
    static PollInterval := 8  ; ~120 checks/sec, well within 16ms frame budget
    
    ; Polling timer handle
    pollTimer := 0
    isActive := false
    
    ; Transition log (for debugging)
    transitionLog := []
    static MaxLogEntries := 100
    
    ; IPC client for fallback vision queries
    ipcClient := ""
    
    ; Callback for state changes (optional)
    onStateChange := ""
    
    ; -----------------------------------------------------------------------
    ; __New(ipcClient)
    ; -----------------------------------------------------------------------
    __New(ipcClient := "") {
        this.ipcClient := ipcClient
    }
    
    ; -----------------------------------------------------------------------
    ; Start() — Begin monitoring mouse mode
    ; -----------------------------------------------------------------------
    Start() {
        if (this.isActive)
            return
        
        this.isActive := true
        this.currentState := MouseMode.STATE_IDLE_2D
        
        timerFn := ObjBindMethod(this, "_Poll")
        SetTimer(timerFn, MouseMode.PollInterval)
        this.pollTimer := timerFn
        
        this._Log("Mouse-mode monitoring started")
    }
    
    ; -----------------------------------------------------------------------
    ; Stop() — Stop monitoring
    ; -----------------------------------------------------------------------
    Stop() {
        if (!this.isActive)
            return
        
        if (this.pollTimer) {
            SetTimer(this.pollTimer, 0)
            this.pollTimer := 0
        }
        
        this.isActive := false
        this.currentState := MouseMode.STATE_IDLE_2D
        this._Log("Mouse-mode monitoring stopped")
    }
    
    ; -----------------------------------------------------------------------
    ; Is3D() — Quick check: are we in 3D camera mode?
    ; -----------------------------------------------------------------------
    Is3D() {
        return (this.currentState = MouseMode.STATE_MODE_3D)
    }
    
    ; -----------------------------------------------------------------------
    ; GetState() — Return current state string
    ; -----------------------------------------------------------------------
    GetState() {
        return this.currentState
    }
    
    ; -----------------------------------------------------------------------
    ; GetTransitionLog() — Return recent state transitions for debugging
    ; -----------------------------------------------------------------------
    GetTransitionLog() {
        return this.transitionLog
    }
    
    ; === Private Methods ===================================================
    
    _Poll() {
        if (!this.isActive)
            return
        
        rmbHeld := GetKeyState("RButton", "P")  ; Physical state
        cursorLocked := this._IsCursorLocked()
        cursorHidden := this._IsCursorHidden()
        
        ; Combined lock signal: cursor is hidden OR confined
        lockConfirmed := (cursorHidden || cursorLocked)
        
        oldState := this.currentState
        
        switch this.currentState {
            case "IDLE_2D":
                if (rmbHeld) {
                    ; RMB pressed — move to pending state
                    this._TransitionTo(MouseMode.STATE_RMB_PENDING)
                } else if (lockConfirmed) {
                    ; Cursor locked without RMB (scroll-zoom first-person)
                    this._TransitionTo(MouseMode.STATE_MODE_3D)
                }
                
            case "RMB_DOWN_PENDING":
                if (!rmbHeld) {
                    ; RMB released before lock confirmed — back to 2D
                    this._TransitionTo(MouseMode.STATE_IDLE_2D)
                } else if (lockConfirmed) {
                    ; Lock confirmed while RMB held — enter 3D mode
                    this._TransitionTo(MouseMode.STATE_MODE_3D)
                }
                
            case "MODE_3D":
                if (!rmbHeld && !lockConfirmed) {
                    ; Both RMB released AND cursor unlocked — back to 2D
                    this._TransitionTo(MouseMode.STATE_IDLE_2D)
                } else if (!lockConfirmed && !rmbHeld) {
                    ; Cursor unlocked (scroll-zoom exit) — back to 2D
                    this._TransitionTo(MouseMode.STATE_IDLE_2D)
                }
        }
    }
    
    ; -----------------------------------------------------------------------
    ; _IsCursorHidden() — Check if the OS cursor is hidden/invisible
    ; Uses GetCursorInfo Windows API
    ; Returns: true if cursor is hidden (Roblox has captured it)
    ; -----------------------------------------------------------------------
    _IsCursorHidden() {
        ; CURSORINFO structure: cbSize (4), flags (4), hCursor (ptr), ptScreenPos.x (4), ptScreenPos.y (4)
        ; Size depends on pointer size
        structSize := 8 + A_PtrSize + 8  ; cbSize + flags + hCursor + POINT
        
        cursorInfo := Buffer(structSize, 0)
        NumPut("UInt", structSize, cursorInfo, 0)  ; cbSize
        
        result := DllCall("GetCursorInfo", "Ptr", cursorInfo)
        if (!result)
            return false
        
        flags := NumGet(cursorInfo, 4, "UInt")
        
        ; CURSOR_SHOWING = 0x00000001
        ; If flags does NOT include CURSOR_SHOWING, the cursor is hidden
        return !(flags & 0x00000001)
    }
    
    ; -----------------------------------------------------------------------
    ; _IsCursorLocked() — Check if the cursor is confined to a region
    ; Uses GetClipCursor Windows API
    ; Returns: true if cursor is confined (clip rect smaller than screen)
    ; -----------------------------------------------------------------------
    _IsCursorLocked() {
        ; RECT structure: left (4), top (4), right (4), bottom (4)
        clipRect := Buffer(16, 0)
        result := DllCall("GetClipCursor", "Ptr", clipRect)
        if (!result)
            return false
        
        clipLeft := NumGet(clipRect, 0, "Int")
        clipTop := NumGet(clipRect, 4, "Int")
        clipRight := NumGet(clipRect, 8, "Int")
        clipBottom := NumGet(clipRect, 12, "Int")
        
        ; Get full virtual screen bounds
        screenLeft := SysGet(76)    ; SM_XVIRTUALSCREEN
        screenTop := SysGet(77)     ; SM_YVIRTUALSCREEN
        screenRight := screenLeft + SysGet(78)   ; SM_CXVIRTUALSCREEN
        screenBottom := screenTop + SysGet(79)    ; SM_CYVIRTUALSCREEN
        
        ; If the clip rect is significantly smaller than the full virtual screen,
        ; the cursor is confined (locked)
        clipW := clipRight - clipLeft
        clipH := clipBottom - clipTop
        screenW := screenRight - screenLeft
        screenH := screenBottom - screenTop
        
        ; Allow a small margin (5px) for rounding
        return (clipW < screenW - 5 || clipH < screenH - 5)
    }
    
    ; -----------------------------------------------------------------------
    ; _TransitionTo(newState) — Change state and log
    ; -----------------------------------------------------------------------
    _TransitionTo(newState) {
        if (newState = this.currentState)
            return
        
        this.previousState := this.currentState
        this.currentState := newState
        
        this._Log(Format("{} → {}", this.previousState, this.currentState))
        
        ; Fire callback if registered
        if (this.onStateChange)
            this.onStateChange.Call(this.previousState, this.currentState)
    }
    
    ; -----------------------------------------------------------------------
    ; _Log(message) — Add to transition log
    ; -----------------------------------------------------------------------
    _Log(message) {
        entry := Format("[{}] {}", FormatTime(, "HH:mm:ss.") A_MSec, message)
        this.transitionLog.Push(entry)
        
        ; Trim old entries
        while (this.transitionLog.Length > MouseMode.MaxLogEntries)
            this.transitionLog.RemoveAt(1)
    }
}
