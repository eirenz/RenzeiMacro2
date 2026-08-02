import tkinter as tk

def get_safe_popup_coords(parent_widget: tk.Widget, popup_w: int, popup_h: int, container_canvas: tk.Widget) -> tuple[int, int]:
    """
    Returns (x, y) coordinates for a popup of given width and height
    such that it NEVER overlaps the active container region, regardless of size.
    It dynamically selects the side (left, right, top, bottom) with the most space.
    """
    root_x = parent_widget.winfo_rootx()
    root_y = parent_widget.winfo_rooty()
    root_w = parent_widget.winfo_width()
    screen_w = parent_widget.winfo_screenwidth()
    screen_h = parent_widget.winfo_screenheight()
    
    # If canvas is missing, fallback safely
    if not container_canvas:
        return root_x + 10, root_y + 100
        
    c_left = container_canvas.winfo_rootx()
    c_top = container_canvas.winfo_rooty()
    
    # Fallback just in case rootx is 0 (unmapped)
    if c_left == 0:
        c_left = root_x + 340 # hardcode left panel width
        c_top = root_y + 46
    c_right = c_left + container_canvas.winfo_width()
    c_bottom = c_top + container_canvas.winfo_height()
    
    # Available space on each side of the container
    space_left = c_left
    space_right = screen_w - c_right
    space_top = c_top
    space_bottom = screen_h - c_bottom
    
    # Find the side with the maximum available space
    best_side = max(
        [
            ("left", space_left),
            ("right", space_right),
            ("top", space_top),
            ("bottom", space_bottom)
        ],
        key=lambda item: item[1]
    )[0]
    
    if best_side == "left":
        # Force the right edge of the popup to be at c_left - 20 (accounts for 15px OS invisible shadow border + 5px margin)
        x = c_left - popup_w - 20
        # Center vertically relative to container, bounded by screen Y
        y = c_top + (c_bottom - c_top - popup_h) // 2
        y = max(0, min(y, screen_h - popup_h - 40))
        return x, y
        
    elif best_side == "right":
        # Force the left edge of the popup to be at c_right + 5
        x = c_right + 5
        # Center vertically relative to container, bounded by screen Y
        y = c_top + (c_bottom - c_top - popup_h) // 2
        y = max(0, min(y, screen_h - popup_h - 40))
        return x, y
        
    elif best_side == "top":
        # Force the bottom edge of the popup to be at c_top - 40 (OS titlebar + margins)
        y = c_top - popup_h - 40
        # Center horizontally relative to container, bounded by screen X
        x = c_left + (c_right - c_left - popup_w) // 2
        x = max(0, min(x, screen_w - popup_w - 20))
        return x, y
        
    else: # bottom
        # Force the top edge of the popup to be at c_bottom + 5
        y = c_bottom + 5
        # Center horizontally relative to container, bounded by screen X
        x = c_left + (c_right - c_left - popup_w) // 2
        x = max(0, min(x, screen_w - popup_w - 20))
        return x, y
