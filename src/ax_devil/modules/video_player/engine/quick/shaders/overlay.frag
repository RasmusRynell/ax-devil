#version 440
// Kinds (vDashKind.y): 0 rounded rect, 1 ellipse, 2 line (rotated rect, dashed along u), 3 glyph, 4 fill.
// Kinds 0-2: vUv is the pixel position inside the shape and vSize its size. Kind 3: vUv is a normalized page
// coordinate and the page is the Alpha8 coverage (read from alpha) tinted by vFill. Kind 4: vFill as interpolated.
// A line's vRadiusStroke.x is its cap radius and .y its dash phase. All colours are premultiplied.
layout(location = 0) in vec2 vUv;
layout(location = 1) in vec2 vSize;
layout(location = 2) in vec2 vRadiusStroke;
layout(location = 3) in vec2 vDashKind;
layout(location = 4) in vec4 vFill;
layout(location = 5) in vec4 vPen;

layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
};
layout(binding = 1) uniform sampler2D page;

const float PI = 3.14159265;

float coverage(float distance) {
    return clamp(0.5 - distance, 0.0, 1.0);
}

float roundedRectDistance(vec2 p, vec2 half_size, float radius) {
    vec2 q = abs(p) - (half_size - vec2(radius));
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - radius;
}

// Approximate signed distance to an ellipse with half sizes h (negative inside).
float ellipseDistance(vec2 p, vec2 h) {
    float k0 = length(p / h);
    float k1 = length(p / (h * h));
    if (k1 < 1e-6) {
        return -min(h.x, h.y);  // The centre; the formula below would say 0.
    }
    return k0 * (k0 - 1.0) / k1;
}

// Clockwise distance along a rounded rect outline from the start of its top edge.
float perimeter(vec2 p, vec2 half_size, float radius) {
    vec2 c = half_size - vec2(radius);
    float a = 2.0 * c.x;
    float b = 2.0 * c.y;
    float arc = 0.5 * PI * radius;
    vec2 v;
    if (p.x > c.x && p.y < -c.y) {
        v = p - vec2(c.x, -c.y);
        return a + atan(v.x, max(-v.y, 1e-6)) * radius;
    }
    if (p.x > c.x && p.y > c.y) {
        v = p - c;
        return a + arc + b + atan(v.y, max(v.x, 1e-6)) * radius;
    }
    if (p.x < -c.x && p.y > c.y) {
        v = p - vec2(-c.x, c.y);
        return 2.0 * a + b + 2.0 * arc + atan(-v.x, max(v.y, 1e-6)) * radius;
    }
    if (p.x < -c.x && p.y < -c.y) {
        v = p + c;
        return 2.0 * a + 2.0 * b + 3.0 * arc + atan(-v.y, max(-v.x, 1e-6)) * radius;
    }
    if (half_size.y - abs(p.y) < half_size.x - abs(p.x)) {
        if (p.y < 0.0) {
            return clamp(p.x + c.x, 0.0, a);
        }
        return a + 2.0 * arc + b + clamp(c.x - p.x, 0.0, a);
    }
    if (p.x > 0.0) {
        return a + arc + clamp(p.y + c.y, 0.0, b);
    }
    return 2.0 * a + b + 3.0 * arc + clamp(c.y - p.y, 0.0, b);
}

void main() {
    float kind = vDashKind.y;
    float dash = vDashKind.x;
    float stroke = vRadiusStroke.y;
    vec4 color;
    if (kind > 3.5) {
        color = vFill;
    } else if (kind > 2.5) {
        color = vFill * texture(page, vUv).a;
    } else {
        vec2 half_size = 0.5 * vSize;
        vec2 p = vUv - half_size;
        if (kind > 1.5) {
            float on = dash > 0.0 ? step(mod(vUv.x + stroke, dash), 0.5 * dash) : 1.0;
            float cap = min(vRadiusStroke.x, half_size.y);
            color = vFill * (coverage(roundedRectDistance(p, half_size, cap)) * on);
        } else {
            float radius = min(vRadiusStroke.x, min(half_size.x, half_size.y));
            float d;
            if (kind > 0.5) {
                d = ellipseDistance(p, half_size);
            } else {
                d = roundedRectDistance(p, half_size, radius);
            }
            float inner = coverage(d + stroke);
            vec4 band = vPen;
            if (kind < 0.5 && dash > 0.0 && stroke > 0.0) {
                // Dashes follow the stroke's centre line.
                float mid = 0.5 * stroke;
                float s = perimeter(p, half_size - vec2(mid), max(radius - mid, 0.0));
                band *= step(mod(s, dash), 0.5 * dash);
            }
            color = (stroke > 0.0 ? mix(band, vFill, inner) : vFill) * coverage(d);
        }
    }
    fragColor = color * qt_Opacity;
}
