# FireWatch — Detección temprana de incendios forestales

Proyecto de título — Ingeniería Informática, Duoc UC San Joaquín, 2025.
Sistema de detección de fuego y humo en tiempo real usando visión por computador
(YOLOv8) desplegado como app web con ONNX Runtime Web, sin instalación en el
dispositivo.

## Estructura del proyecto

```
firewatch-project/
├── web/
│   └── index.html          → Página informativa pública del proyecto
├── testing/
│   └── test_ensemble.html  → Página de prueba para validar Modelo A (fire)
│                              + Modelo B (smoke) corriendo en paralelo
├── docs/                    → (vacío) documentación técnica, diagramas, memoria
└── README.md                → este archivo
```

## Estado actual

- ✅ Modelo A (fire) entrenado en 3 rondas: base + Forest Fire + refuerzo
  Fase 4 (falsos positivos: nubes, ciudad día/noche, niebla, fireworks)
- ✅ Modelo B (smoke) entrenado en 3 rondas: base (FireEye + kutaykutlu)
  + smoke-fire-detection-yolo (anotaciones reales) + refuerzo Fase 4
- ⚠️ **Pendiente de validar:** ambos modelos con fuego/humo real de día
  (fósforo, soplete). Última prueba con imágenes en pantalla de noche
  mostró falsos positivos — podría deberse a artefactos de pantalla/luz
  artificial, no necesariamente al modelo. Ver conversación de diseño
  para el protocolo de prueba sugerido.
- ✅ Ambos modelos exportados a ONNX (`modelo_a_fire.onnx`, `modelo_b_smoke.onnx`)
  — **no incluidos en este zip**, debes copiarlos tú a `testing/` para probar
- ✅ Página web informativa (`web/index.html`) — revisar métricas de Modelo B
  antes de publicar, quedaron con nota de advertencia pendiente de confirmación

## Cómo probar los modelos

1. Copia `modelo_a_fire.onnx` y `modelo_b_smoke.onnx` dentro de `testing/`
2. Desde esa carpeta: `python -m http.server 8080`
3. Desde el celular (misma red WiFi): `http://<IP-local>:8080/test_ensemble.html`
4. Carga ambos modelos con los selectores de archivo y activa la cámara

## Cómo ver la página web

Desde `web/`: `python -m http.server 8080` → abrir `http://localhost:8080`

## Arquitectura completa planeada (roadmap)

```
Cliente de detección (cámara/celular/PC)
        │  foto + GPS + alerta
        ▼
Backend API + base de datos   ← NO CONSTRUIDO AÚN
        │  reportes + historial
        ▼
App de escritorio (bomberos, acceso restringido)   ← NO CONSTRUIDO AÚN
```

Componentes pendientes, en orden sugerido de prioridad:

1. **Backend API + base de datos** — recibe reportes (foto, GPS, nivel de
   alerta) desde el cliente de detección y los sirve a la app de escritorio.
   Núcleo técnico del sistema, prioridad más alta.
2. **Lógica de semáforo con cooldown** — verde: sin reporte · amarillo: 1
   alerta con foto, 5 min sin repetir salvo escalamiento · rojo: alerta cada
   3 min a las 3 compañías de bomberos más cercanas, hasta ser marcada como
   controlada por un bombero.
3. **App de escritorio** — historial de reportes, acceso restringido
   (ej. bomberos), marcado de falso positivo / confirmado / controlado.
4. **Software de cámara/detección** — versión para correr de forma
   desatendida en un PC conectado a cámara fija o de vigilancia (no en la
   cámara misma, la mayoría no tiene cómputo propio).

## Notas técnicas importantes

- GPS: en celular se lee en vivo (`navigator.geolocation`); en cámara fija
  debe registrarse manualmente la coordenada al instalar el equipo.
- Evitar reportes duplicados de cámaras cercanas apuntando al mismo evento
  (agrupar por ventana de tiempo + radio geográfico).
- Estaciones de bomberos: mantener tabla propia verificada manualmente,
  no depender de búsqueda en vivo de Google Places para algo tan crítico.

## Autor

Benjamín I. Morales Madrid · Ingeniería Informática · Duoc UC San Joaquín · 2025
