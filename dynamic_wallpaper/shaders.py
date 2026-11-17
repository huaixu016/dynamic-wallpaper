"""Compose display modes from one decoded RGB texture before color-managed output."""

from .models import DisplayMode, display_geometry


def tile_shader(width: int, height: int, video_width: int, video_height: int) -> str:
    if min(width, height, video_width, video_height) <= 0:
        raise ValueError("屏幕和视频的尺寸必须大于零。")
    # mpv presents an oriented texture at MAIN; use its displayed size, including pixel aspect ratio.
    return f"""//!HOOK MAIN
//!BIND HOOKED
//!WIDTH {width}
//!HEIGHT {height}
//!DESC LocalDynamicWallpaper native-size tiling
vec4 hook() {{
    vec2 pixel = HOOKED_pos * vec2({width}.0, {height}.0);
    vec2 uv = fract(pixel / vec2({video_width}.0, {video_height}.0));
    return HOOKED_tex(uv);
}}
"""


def _blur_pass(texture: str, output: str, width: int, height: int, horizontal: bool) -> str:
    direction = "1.0, 0.0" if horizontal else "0.0, 1.0"
    return f"""//!HOOK MAIN
//!BIND {texture}
//!SAVE {output}
//!WIDTH {width}
//!HEIGHT {height}
//!DESC LocalDynamicWallpaper edge blur
vec4 edge_sample(vec2 uv) {{
    vec2 inset = 0.5 / {texture}_size;
    return {texture}_tex(clamp(uv, inset, 1.0 - inset));
}}
vec4 hook() {{
    vec2 uv = {texture}_pos;
    vec2 step = vec2({direction}) / {texture}_size;
    return edge_sample(uv) * 0.2270270270
        + (edge_sample(uv + step * 1.3846153846) + edge_sample(uv - step * 1.3846153846)) * 0.3162162162
        + (edge_sample(uv + step * 3.2307692308) + edge_sample(uv - step * 3.2307692308)) * 0.0702702703;
}}
"""


def _edge_preview(video_width: int, video_height: int) -> str:
    low_width, low_height = min(video_width, 128), min(video_height, 192)
    # SAVE retains a small auxiliary texture without replacing the original MAIN frame.
    source = f"""//!HOOK MAIN
//!BIND HOOKED
//!SAVE WALLPAPER_EDGE_LOW
//!WIDTH {low_width}
//!HEIGHT {low_height}
//!DESC LocalDynamicWallpaper edge preview
vec4 hook() {{ return HOOKED_tex(HOOKED_pos); }}
"""
    source += _blur_pass("WALLPAPER_EDGE_LOW", "WALLPAPER_EDGE_X", low_width, low_height, True)
    source += _blur_pass("WALLPAPER_EDGE_X", "WALLPAPER_EDGE_BLUR", low_width, low_height, False)
    return source


def contain_shader(width: int, height: int, video_width: int, video_height: int) -> str | None:
    left, _top, fitted_width, _fitted_height = display_geometry(
        width, height, video_width, video_height, DisplayMode.CONTAIN)
    if fitted_width == width:
        return None
    source = _edge_preview(video_width, video_height)
    right_start = left + fitted_width
    source += f"""//!HOOK MAIN
//!BIND HOOKED
//!BIND WALLPAPER_EDGE_BLUR
//!WIDTH {width}
//!HEIGHT {height}
//!DESC LocalDynamicWallpaper contain with blurred side edges
vec4 hook() {{
    vec2 pixel = HOOKED_pos * vec2({width}.0, {height}.0);
    if (pixel.x >= {left}.0 && pixel.x < {right_start}.0) {{
        vec2 uv = vec2((pixel.x - {left}.0) / {fitted_width}.0, pixel.y / {height}.0);
        return HOOKED_tex(uv);
    }}
    // Extend each corresponding 8% edge strip outward; never blur the fitted main image.
    float edge_x = pixel.x < {left}.0
        ? (1.0 - pixel.x / {max(left, 1)}.0) * 0.08
        : 1.0 - (pixel.x - {right_start}.0) / {max(width - right_start, 1)}.0 * 0.08;
    vec2 inset = 0.5 / WALLPAPER_EDGE_BLUR_size;
    vec2 uv = clamp(vec2(edge_x, pixel.y / {height}.0), inset, 1.0 - inset);
    return WALLPAPER_EDGE_BLUR_tex(uv);
}}
"""
    return source


def center_shader(width: int, height: int, video_width: int, video_height: int) -> str | None:
    left, top, _, _ = display_geometry(width, height, video_width, video_height, DisplayMode.CENTER)
    if video_width >= width and video_height >= height:
        return None
    right, bottom = left + video_width, top + video_height
    source = _edge_preview(video_width, video_height)
    source += f"""//!HOOK MAIN
//!BIND HOOKED
//!BIND WALLPAPER_EDGE_BLUR
//!WIDTH {width}
//!HEIGHT {height}
//!DESC LocalDynamicWallpaper native-size center with blurred edges
vec4 hook() {{
    vec2 pixel = HOOKED_pos * vec2({width}.0, {height}.0);
    vec2 uv = (pixel - vec2({left}.0, {top}.0)) / vec2({video_width}.0, {video_height}.0);
    if (pixel.x >= {left}.0 && pixel.x < {right}.0
            && pixel.y >= {top}.0 && pixel.y < {bottom}.0) {{
        return HOOKED_tex(uv);
    }}
    // Clamp each blank axis to its matching edge strip; corners use the corresponding corner image.
    if (pixel.x < {left}.0)
        uv.x = (1.0 - pixel.x / {max(left, 1)}.0) * 0.08;
    else if (pixel.x >= {right}.0)
        uv.x = 1.0 - (pixel.x - {right}.0) / {max(width - right, 1)}.0 * 0.08;
    if (pixel.y < {top}.0)
        uv.y = (1.0 - pixel.y / {max(top, 1)}.0) * 0.08;
    else if (pixel.y >= {bottom}.0)
        uv.y = 1.0 - (pixel.y - {bottom}.0) / {max(height - bottom, 1)}.0 * 0.08;
    vec2 inset = 0.5 / WALLPAPER_EDGE_BLUR_size;
    return WALLPAPER_EDGE_BLUR_tex(clamp(uv, inset, 1.0 - inset));
}}
"""
    return source
