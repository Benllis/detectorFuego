# Guía para quien presta su cámara a FireWatch

*(Borrador del equipo — ver "Antes de entregarla" al final)*

Gracias por considerar conectar tu cámara. Esta guía explica, sin tecnicismos, **qué hace FireWatch con tu cámara, qué no hace y qué control mantienes tú**.

## Qué es FireWatch

Es un proyecto académico (Duoc UC) que intenta detectar **humo o fuego en el cerro o el bosque** a partir de imágenes de cámaras y avisar a una persona que revisa cada alerta. **No es un sistema de seguridad certificado ni reemplaza a Bomberos.** Puede equivocarse (ver abajo). Si ves fuego o humo, llama a Bomberos (**132**) o a CONAF (**130**); no esperes un aviso del programa.

## Qué necesitamos de ti

- Una cámara IP con Wi-Fi o cable (por ejemplo Tapo, Hikvision, Dahua/Imou, Reolink, Foscam o Axis) que ya tengas instalada y encendida todo el día.
- Que apunte hacia una zona de **cerro, bosque o campo**, no hacia casas, patios, calles o personas.
- La cámara y el computador de FireWatch en la **misma red**.
- Una **cuenta exclusiva** para FireWatch dentro de la cámara (abajo).

## Cómo se conecta (lo hacemos juntos)

1. **Crea una cuenta nueva en tu cámara** solo para FireWatch, con una contraseña que no uses en ningún otro lado. En las Tapo: app Tapo → Ajustes de la cámara → Avanzado → *Cuenta de la cámara*. En otras marcas, en la configuración del equipo; algunas piden activar "RTSP" u "ONVIF".
2. **No nos envíes la contraseña por chat ni por correo.** La escribes tú misma/o en el programa cuando estemos contigo, o la dictas en persona. Queda guardada solo en el computador de FireWatch (en un archivo local de ese equipo).
3. Conviene que la cámara tenga siempre la misma dirección en tu red (en el router: "reserva de IP" o "IP fija"); si cambia, FireWatch pierde la señal hasta que se actualice.
4. Con el botón **Probar conexión** vemos una imagen en pantalla antes de guardar nada. Tú ves lo mismo que verá el programa.
5. Ajustamos la **ubicación** (coordenadas) de la cámara para poder mostrarla en el mapa. Puede ser la dirección o un punto aproximado: tú decides con qué precisión la compartes.

## Qué hace el programa con las imágenes

- **Analiza las imágenes al instante, en el computador de FireWatch, y no graba video.**
- **Solo guarda una foto cuando cree que hay fuego o humo** (o cuando alguien del equipo la envía a mano). Esa foto viaja al servidor del equipo, en la misma red, y la revisa una persona.
- Esa foto puede mostrar lo que haya a la vista. Por eso importa hacia dónde apunta la cámara. **El programa no difumina personas ni patentes.**
- Las imágenes no se publican ni se muestran en ningún sitio abierto: solo las ve quien tenga una cuenta del sistema (operadores y bomberos autorizados).
- El mapa y el programa de escritorio muestran la ubicación de la cámara a quienes tengan cuenta.

## Qué puede salir mal

- **Falsas alarmas:** la niebla, las nubes, el polvo o una luz pueden confundirse con humo o fuego; la persona que revisa descarta esos casos.
- **Fuegos que no detecta:** en nuestras pruebas el programa pasa por alto una parte importante de los casos. Por eso no sirve como único aviso.
- Si se corta Internet o la red, la cámara deja de enviar hasta que vuelva; las alertas pendientes se reenvían solas.

## Tu control

Puedes dejar de participar **cuando quieras**, sin dar explicaciones:

- Desconecta la cámara de la red o apágala.
- Cambia la contraseña de la cuenta de FireWatch en tu cámara: el programa pierde el acceso al instante.
- Pídenos retirarla y **eliminar las fotos** que se hayan guardado de tu cámara.
- Gira o tapa la cámara si quieres evitar una zona.

## Antes de entregarla (para el equipo, no para el dueño)

Esta guía describe lo que el sistema hace hoy; hay tres decisiones nuestras que **faltan y no deben quedar implícitas**:

1. **Cuánto tiempo guardamos las fotos.** Hoy no se borran solas: quedan en `alertas_enviadas/` (equipo de la cámara) y en la carpeta de alertas del backend. Definir un plazo (por ejemplo, borrar las descartadas a los 30 días) y escribirlo aquí, o quitar la frase "eliminar las fotos" si no podemos cumplirla.
2. **Consentimiento escrito.** Un acuerdo breve y firmado (quién presta, qué cámara, apuntando a dónde, desde cuándo, cómo retirarse). Si la cámara es de una comunidad o un edificio, que firme quien tenga autoridad para hacerlo.
3. **Contacto.** Un nombre y un teléfono o correo del equipo para que el dueño pueda retirarse o preguntar.

Además: revisar que la cámara no capture espacios privados antes de conectarla, y explicar al dueño que **los modelos aún tienen falsas alarmas y fallan en parte de los casos** (cifras en el README de la cámara).
