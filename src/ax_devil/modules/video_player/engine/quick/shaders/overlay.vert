#version 440
layout(location = 0) in vec2 position;
layout(location = 1) in vec2 uv;
layout(location = 2) in vec2 size;
layout(location = 3) in vec2 radiusStroke;
layout(location = 4) in vec2 dashKind;
layout(location = 5) in vec4 fill;
layout(location = 6) in vec4 pen;

layout(location = 0) out vec2 vUv;
layout(location = 1) out vec2 vSize;
layout(location = 2) out vec2 vRadiusStroke;
layout(location = 3) out vec2 vDashKind;
layout(location = 4) out vec4 vFill;
layout(location = 5) out vec4 vPen;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
};

void main() {
    vUv = uv;
    vSize = size;
    vRadiusStroke = radiusStroke;
    vDashKind = dashKind;
    vFill = fill;
    vPen = pen;
    gl_Position = qt_Matrix * vec4(position, 0.0, 1.0);
}
