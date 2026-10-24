"""GPU-backed QPainter surface for the JARVIS holographic face.

The rest of the HUD remains an ordinary QWidget canvas. This small transparent
QOpenGLWidget draws only the face, so Qt can use its OpenGL paint engine without
changing the layout or the other HUD panels. Animation and mesh preparation
remain in :mod:`core.avatar` on the CPU.
"""

from __future__ import annotations

import traceback

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QSurfaceFormat, QVector3D
from PyQt6 import sip
from PyQt6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLFunctions_2_0,
    QOpenGLShader,
    QOpenGLShaderProgram,
)
from PyQt6.QtOpenGLWidgets import QOpenGLWidget


class HoloAvatarGPUWidget(QOpenGLWidget):
    """Transparent, mouse-through OpenGL surface for one animated avatar."""

    ready = pyqtSignal()

    def __init__(self, avatar, parent=None) -> None:
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        fmt.setVersion(2, 0)
        fmt.setAlphaBufferSize(8)
        self.setFormat(fmt)

        # The face is an overlay over the parent's aurora background. Qt needs
        # an alpha channel and the GL widget must sit above the parent painter
        # for those transparent pixels to reveal it.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAutoFillBackground(False)

        self._avatar = avatar
        self._initialized = False
        self._scene = None
        self._paint_error_reported = False
        self._surface_program = None
        self._surface_vbo = None
        self._surface_attrs = None
        self._surface_ready = False
        self._gl = None
        self._surface_render_reported = False
        self._surface_render_error_reported = False

        # Mesh connectivity is fixed for this avatar. Build the unique wire
        # edges and their owning faces once instead of sorting/deduplicating
        # every edge on every animation frame.
        self._coarse_edges, self._coarse_edge_faces = self._build_edge_topology(
            avatar._fa, avatar._fb, avatar._fc
        )
        self._coarse_face_visible = np.zeros(len(avatar._fa), dtype=bool)
        self._coarse_edge_visible = np.zeros(len(self._coarse_edges), dtype=bool)

    @staticmethod
    def _build_edge_topology(face_a, face_b, face_c):
        """Return unique undirected edges and their adjacent triangle indices."""
        if not len(face_a):
            return (
                np.empty((0, 2), dtype=np.int32),
                np.empty((0, 0), dtype=np.int32),
            )
        face_edges = np.stack(
            (
                np.stack((face_a, face_b), axis=1),
                np.stack((face_b, face_c), axis=1),
                np.stack((face_c, face_a), axis=1),
            ),
            axis=1,
        )
        face_edges.sort(axis=2)
        edges, inverse = np.unique(
            face_edges.reshape(-1, 2), axis=0, return_inverse=True
        )

        counts = np.bincount(inverse, minlength=len(edges))
        owners = np.repeat(np.arange(len(face_a), dtype=np.int32), 3)
        sort_order = np.argsort(inverse, kind="stable")
        starts = np.cumsum(counts, dtype=np.int64) - counts
        slots = np.arange(inverse.size, dtype=np.int64) - np.repeat(starts, counts)
        adjacent_faces = np.full(
            (len(edges), int(counts.max())), -1, dtype=np.int32
        )
        adjacent_faces[inverse[sort_order], slots] = owners[sort_order]
        return edges.astype(np.int32, copy=False), adjacent_faces

    def initializeGL(self) -> None:
        self._initialized = True
        try:
            self._gl = QOpenGLFunctions_2_0()
            if not self._gl.initializeOpenGLFunctions():
                raise RuntimeError("could not initialize OpenGL 2.0 functions")
            self._initialize_surface_shader()
            context = self.context()
            fmt = context.format() if context is not None else None
            if context is not None and context.isValid() and fmt is not None:
                print(
                    "[GPU] Holographic face OpenGL context ready "
                    f"({fmt.majorVersion()}.{fmt.minorVersion()})."
                )
            else:
                print("[GPU] Holographic face OpenGL context is unavailable.")
        except Exception as exc:
            print(f"[GPU] Could not inspect the OpenGL face context: {exc}")
        self.ready.emit()

    @property
    def gpu_ready(self) -> bool:
        return self._initialized and self.isValid() and self._surface_ready

    def _initialize_surface_shader(self) -> None:
        """Build a smooth-shaded mesh pipeline for the face surface."""
        program = QOpenGLShaderProgram(self)
        vertex = """
            #version 120
            attribute vec3 aPosition;
            attribute vec3 aNormal;
            attribute float aFade;
            varying vec3 vNormal;
            varying float vFade;
            void main() {
                gl_Position = vec4(aPosition, 1.0);
                vNormal = aNormal;
                vFade = aFade;
            }
        """
        fragment = """
            #version 120
            uniform vec3 uPrimary;
            uniform vec3 uBackground;
            uniform float uEnergy;
            uniform float uWire;
            varying vec3 vNormal;
            varying float vFade;
            void main() {
                if (uWire > 0.5) {
                    vec3 wireColor = mix(uPrimary, vec3(0.30, 0.83, 1.0), 0.62);
                    gl_FragColor = vec4(wireColor, 0.19 * clamp(vFade, 0.0, 1.0));
                    return;
                }
                vec3 n = normalize(vNormal);
                vec3 lightDir = normalize(vec3(-0.55, 0.50, 0.52));
                float nz = clamp(n.z, 0.0, 1.0);
                float fresnel = pow(clamp(1.0 - nz, 0.0, 1.0), 1.8);
                float diffuse = clamp(dot(n, lightDir), 0.0, 1.0);
                float brightness = 0.42 + 0.24 * fresnel + 0.67 * pow(diffuse, 1.1);
                brightness *= clamp(vFade, 0.0, 1.0) * uEnergy;
                vec3 holoBlue = mix(uPrimary, vec3(0.04, 0.40, 0.78), 0.70);
                vec3 color = mix(uBackground, holoBlue, clamp(brightness, 0.0, 1.0));
                float specular = pow(max(dot(reflect(-lightDir, n), vec3(0.0, 0.0, 1.0)), 0.0), 22.0);
                color += vec3(0.16, 0.34, 0.62) * specular * 0.22;
                gl_FragColor = vec4(color, 0.96);
            }
        """
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex):
            raise RuntimeError(program.log())
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment):
            raise RuntimeError(program.log())
        program.bindAttributeLocation("aPosition", 0)
        program.bindAttributeLocation("aNormal", 1)
        program.bindAttributeLocation("aFade", 2)
        if not program.link():
            raise RuntimeError(program.log())

        vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        if not vbo.create():
            raise RuntimeError("could not create hologram vertex buffer")
        vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.StreamDraw)

        self._surface_program = program
        self._surface_vbo = vbo
        self._surface_attrs = (
            program.attributeLocation("aPosition"),
            program.attributeLocation("aNormal"),
            program.attributeLocation("aFade"),
        )
        self._surface_ready = all(i >= 0 for i in self._surface_attrs)
        if self._surface_ready:
            print("[GPU] Smooth face shader and streaming mesh buffer ready.")
        else:
            print("[GPU] Face shader attributes are unavailable; using painter fallback.")

    def set_scene(
        self,
        cx: float,
        cy: float,
        radius: float,
        primary: QColor,
        accent: QColor,
        background: QColor,
    ) -> None:
        self._scene = (
            float(cx),
            float(cy),
            float(radius),
            QColor(primary),
            QColor(accent),
            QColor(background),
        )

    def clear_scene(self) -> None:
        self._scene = None

    def paintGL(self) -> None:
        # Qt has already made this widget's context and framebuffer current.
        # Painting here keeps QPainter on the OpenGL widget's supported render
        # path and avoids bypassing QOpenGLWidget's framebuffer preparation.
        painter = None
        try:
            painter = QPainter(self)
            if not painter.isActive():
                return

            # Clear only this GL layer. Source composition preserves alpha so
            # the parent's background and HUD chrome remain visible beneath it.
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_Source
            )
            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )

            scene = self._scene
            if scene is not None:
                cx, cy, radius, primary, accent, background = scene
                self._avatar.paint(
                    painter,
                    cx,
                    cy,
                    radius,
                    primary,
                    accent,
                    background,
                    draw_aura=False,
                    surface_renderer=self._draw_surface_gpu if self._surface_ready else None,
                )
        except Exception:
            if not self._paint_error_reported:
                self._paint_error_reported = True
                print("[GPU] Holographic face paint failed; keeping JARVIS alive.")
                traceback.print_exc()
        finally:
            if painter is not None and painter.isActive():
                try:
                    painter.end()
                except Exception:
                    pass

    def _draw_surface_gpu(
        self,
        painter: QPainter,
        xs,
        ys,
        normals,
        verts,
        fade,
        primary: QColor,
        background: QColor,
        amp: float,
        face_a,
        face_b,
        face_c,
        groups,
    ) -> bool:
        """Stream visible triangles to OpenGL; the fragment shader shades smoothly."""
        a, b, c = face_a, face_b, face_c
        face_n = np.cross(verts[b] - verts[a], verts[c] - verts[a])
        face_n /= np.maximum(np.linalg.norm(face_n, axis=1, keepdims=True), 1e-9)
        reference = normals[a] + normals[b] + normals[c]
        face_n *= np.sign((face_n * reference).sum(1))[:, None]
        area = np.abs(
            (xs[b] - xs[a]) * (ys[c] - ys[a]) - (xs[c] - xs[a]) * (ys[b] - ys[a])
        )
        visible = np.flatnonzero((face_n[:, 2] > 0.015) & (area > 3.0))
        if not visible.size:
            return True

        depth = (verts[a, 2][visible] + verts[b, 2][visible] + verts[c, 2][visible]) / 3.0
        order = np.argsort(groups[visible] * 1000.0 + depth, kind="stable")
        tri = np.stack((a[visible], b[visible], c[visible]), axis=1)[order].reshape(-1)
        width = max(1.0, float(self.width()))
        height = max(1.0, float(self.height()))
        data = np.empty((tri.size, 7), dtype=np.float32)
        data[:, 0] = xs[tri] * (2.0 / width) - 1.0
        data[:, 1] = 1.0 - ys[tri] * (2.0 / height)
        data[:, 2] = 0.0
        data[:, 3:6] = normals[tri]
        data[:, 6] = fade[tri]
        data = np.ascontiguousarray(data)

        painter.beginNativePainting()
        try:
            functions = self._gl
            functions.glViewport(
                0, 0,
                int(round(self.width() * self.devicePixelRatioF())),
                int(round(self.height() * self.devicePixelRatioF())),
            )
            functions.glDisable(0x0B71)  # GL_DEPTH_TEST; painter-sorted transparency
            functions.glEnable(0x0BE2)   # GL_BLEND
            functions.glBlendFunc(0x0302, 0x0303)  # SRC_ALPHA, ONE_MINUS_SRC_ALPHA

            program = self._surface_program
            vbo = self._surface_vbo
            loc_pos, loc_norm, loc_fade = self._surface_attrs
            if not program.bind() or not vbo.bind():
                return False
            vbo.allocate(sip.voidptr(data.ctypes.data), data.nbytes)
            stride = 7 * data.dtype.itemsize
            program.setAttributeBuffer(loc_pos, 0x1406, 0, 3, stride)  # GL_FLOAT
            program.setAttributeBuffer(loc_norm, 0x1406, 3 * data.dtype.itemsize, 3, stride)
            program.setAttributeBuffer(loc_fade, 0x1406, 6 * data.dtype.itemsize, 1, stride)
            for loc in (loc_pos, loc_norm, loc_fade):
                program.enableAttributeArray(loc)
            program.setUniformValue(
                "uPrimary", QVector3D(primary.redF(), primary.greenF(), primary.blueF())
            )
            program.setUniformValue(
                "uBackground", QVector3D(
                    background.redF(), background.greenF(), background.blueF()
                )
            )
            program.setUniformValue("uEnergy", float(0.88 + 0.24 * amp))
            program.setUniformValue("uWire", 0.0)
            functions.glDrawArrays(0x0004, 0, int(data.shape[0]))  # GL_TRIANGLES

            # The selected Prism reference has a fine triangular hologram
            # lattice. Draw its subtle edges on the GPU over the shaded face.
            coarse_a, coarse_b, coarse_c = (
                self._avatar._fa,
                self._avatar._fb,
                self._avatar._fc,
            )
            coarse_vertices = verts[: len(self._avatar._v0)]
            coarse_normals = normals[: len(self._avatar._v0)]
            face_normals = np.cross(
                coarse_vertices[coarse_b] - coarse_vertices[coarse_a],
                coarse_vertices[coarse_c] - coarse_vertices[coarse_a],
            )
            face_normals /= np.maximum(
                np.linalg.norm(face_normals, axis=1, keepdims=True), 1e-9
            )
            reference = (
                coarse_normals[coarse_a]
                + coarse_normals[coarse_b]
                + coarse_normals[coarse_c]
            )
            face_normals *= np.sign((face_normals * reference).sum(1))[:, None]
            face_area = np.abs(
                (xs[coarse_b] - xs[coarse_a]) * (ys[coarse_c] - ys[coarse_a])
                - (xs[coarse_c] - xs[coarse_a]) * (ys[coarse_b] - ys[coarse_a])
            )
            visible_faces = np.flatnonzero(
                (face_normals[:, 2] > 0.015) & (face_area > 4.0)
            )
            face_visible = self._coarse_face_visible
            face_visible.fill(False)
            face_visible[visible_faces] = True
            edge_visible = self._coarse_edge_visible
            edge_visible.fill(False)
            adjacent_faces = self._coarse_edge_faces
            for slot in range(adjacent_faces.shape[1]):
                owners = adjacent_faces[:, slot]
                valid = owners >= 0
                edge_visible[valid] |= face_visible[owners[valid]]
            edges = self._coarse_edges[edge_visible]
            edge_length = np.hypot(
                xs[edges[:, 0]] - xs[edges[:, 1]],
                ys[edges[:, 0]] - ys[edges[:, 1]],
            )
            edges = edges[edge_length >= 6.0]
            edge_vertices = edges.reshape(-1)
            lines = np.zeros((edge_vertices.size, 7), dtype=np.float32)
            lines[:, 0] = xs[edge_vertices] * (2.0 / width) - 1.0
            lines[:, 1] = 1.0 - ys[edge_vertices] * (2.0 / height)
            lines[:, 3:6] = normals[edge_vertices]
            lines[:, 6] = fade[edge_vertices]
            lines = np.ascontiguousarray(lines)
            vbo.allocate(sip.voidptr(lines.ctypes.data), lines.nbytes)
            program.setUniformValue("uWire", 1.0)
            functions.glLineWidth(1.0)
            functions.glDrawArrays(0x0001, 0, int(lines.shape[0]))  # GL_LINES
            program.setUniformValue("uWire", 0.0)
            gl_error = functions.glGetError()
            if gl_error and not self._surface_render_error_reported:
                self._surface_render_error_reported = True
                print(f"[GPU] Face draw reported OpenGL error 0x{gl_error:04x}.")
            if not self._surface_render_reported:
                self._surface_render_reported = True
                print(f"[GPU] Smooth face surface is drawing {data.shape[0] // 3} triangles.")
            for loc in (loc_pos, loc_norm, loc_fade):
                program.disableAttributeArray(loc)
            vbo.release()
            program.release()
            return True
        except Exception as exc:
            if not self._surface_render_error_reported:
                self._surface_render_error_reported = True
                print(f"[GPU] Smooth face draw failed; falling back to painter: {exc}")
            return False
        finally:
            painter.endNativePainting()
