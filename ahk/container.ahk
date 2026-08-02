; ============================================================================
; container.ahk — Container Detection & Normalized Coordinate Math
; ============================================================================
; The "Container" = the Roblox client window's client-area rectangle.
; All recorded actions and vision scans use normalized coordinates (0.0–1.0)
; relative to this container, never absolute screen pixels.
; ============================================================================

#Requires AutoHotkey v2.0

class Container {
    ; Current container rectangle (absolute screen coordinates of client area)
    x := 0
    y := 0
    w := 0
    h := 0
    
    ; Whether the container has been successfully detected
    isValid := false
    
    ; Window handle
    hwnd := 0
    
    ; Process name to search for
    static ProcessName := "RobloxPlayerBeta.exe"
    
    ; Window class patterns Roblox may use
    static WindowClasses := ["WINDOWSCLIENT", "RobloxPlayerBeta"]
    
    ; -----------------------------------------------------------------------
    ; AutoDetect() — Find the Roblox window and extract its client-area rect
    ; Returns: true if found, false otherwise
    ; -----------------------------------------------------------------------
    AutoDetect() {
        ; Try finding by process name first
        this.hwnd := this._FindByProcess()
        
        if (!this.hwnd) {
            ; Fallback: try finding by window class
            this.hwnd := this._FindByClass()
        }
        
        if (!this.hwnd) {
            ; Fallback: try finding by title containing "Roblox"
            this.hwnd := this._FindByTitle()
        }
        
        if (!this.hwnd) {
            this.isValid := false
            return false
        }
        
        return this._ExtractClientRect()
    }
    
    ; -----------------------------------------------------------------------
    ; SetManual(x, y, w, h) — Manual override for the container rect
    ; -----------------------------------------------------------------------
    SetManual(x, y, w, h) {
        this.x := x
        this.y := y
        this.w := w
        this.h := h
        this.isValid := (w > 0 && h > 0)
        return this.isValid
    }
    
    ; -----------------------------------------------------------------------
    ; Refresh() — Re-detect the container rect (call before playback, etc.)
    ; Returns: true if still valid
    ; -----------------------------------------------------------------------
    Refresh() {
        if (this.hwnd && WinExist("ahk_id " this.hwnd)) {
            return this._ExtractClientRect()
        }
        return this.AutoDetect()
    }
    
    ; -----------------------------------------------------------------------
    ; NormalizeCoords(absX, absY) — Convert absolute screen coords to 0.0–1.0
    ; Returns: {nx: Float, ny: Float}
    ; -----------------------------------------------------------------------
    NormalizeCoords(absX, absY) {
        if (!this.isValid || this.w = 0 || this.h = 0)
            throw Error("Container is not valid — cannot normalize coordinates")
        
        nx := (absX - this.x) / this.w
        ny := (absY - this.y) / this.h
        return {nx: nx, ny: ny}
    }
    
    ; -----------------------------------------------------------------------
    ; DenormalizeCoords(nx, ny) — Convert 0.0–1.0 coords to absolute screen
    ; Returns: {absX: Integer, absY: Integer}
    ; -----------------------------------------------------------------------
    DenormalizeCoords(nx, ny) {
        if (!this.isValid)
            throw Error("Container is not valid — cannot denormalize coordinates")
        
        absX := Round(this.x + nx * this.w)
        absY := Round(this.y + ny * this.h)
        return {absX: absX, absY: absY}
    }
    
    ; -----------------------------------------------------------------------
    ; IsPointInContainer(absX, absY) — Check if an absolute point is within
    ; Returns: true/false
    ; -----------------------------------------------------------------------
    IsPointInContainer(absX, absY) {
        if (!this.isValid)
            return false
        return (absX >= this.x && absX < this.x + this.w
             && absY >= this.y && absY < this.y + this.h)
    }
    
    ; -----------------------------------------------------------------------
    ; GetRect() — Return current container as a Map for IPC/serialization
    ; -----------------------------------------------------------------------
    GetRect() {
        return Map(
            "x", this.x,
            "y", this.y,
            "w", this.w,
            "h", this.h,
            "valid", this.isValid
        )
    }
    
    ; -----------------------------------------------------------------------
    ; ToString() — Debug representation
    ; -----------------------------------------------------------------------
    ToString() {
        if (!this.isValid)
            return "Container [INVALID]"
        return Format("Container [{}, {} — {}x{}] hwnd={}", this.x, this.y, this.w, this.h, this.hwnd)
    }
    
    ; === Private Methods ===================================================
    
    ; Find Roblox window by process name
    _FindByProcess() {
        try {
            hwnd := WinExist("ahk_exe " Container.ProcessName)
            return hwnd
        }
        return 0
    }
    
    ; Find Roblox window by known window classes
    _FindByClass() {
        for className in Container.WindowClasses {
            try {
                hwnd := WinExist("ahk_class " className)
                if (hwnd)
                    return hwnd
            }
        }
        return 0
    }
    
    ; Find Roblox window by title
    _FindByTitle() {
        try {
            hwnd := WinExist("Roblox")
            return hwnd
        }
        return 0
    }
    
    ; Extract the client-area rectangle from the window handle
    _ExtractClientRect() {
        if (!this.hwnd)
            return false
        
        ; Get client area rect (relative to client area, so left/top = 0)
        clientRect := Buffer(16, 0)
        result := DllCall("GetClientRect", "Ptr", this.hwnd, "Ptr", clientRect)
        if (!result) {
            this.isValid := false
            return false
        }
        
        clientW := NumGet(clientRect, 8, "Int")   ; right
        clientH := NumGet(clientRect, 12, "Int")   ; bottom
        
        ; Convert client (0,0) to screen coordinates
        point := Buffer(8, 0)
        NumPut("Int", 0, point, 0)
        NumPut("Int", 0, point, 4)
        DllCall("ClientToScreen", "Ptr", this.hwnd, "Ptr", point)
        
        this.x := NumGet(point, 0, "Int")
        this.y := NumGet(point, 4, "Int")
        this.w := clientW
        this.h := clientH
        this.isValid := (this.w > 0 && this.h > 0)
        
        return this.isValid
    }
}
