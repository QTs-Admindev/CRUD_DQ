-- Estado del vigía de la plataforma (functions/monitoring/plataforma.py): una fila
-- por servicio vigilado, para saber cuántas revisiones lleva fallando y cuándo se avisó.
-- Correr con el usuario master ANTES de desplegar el código.


CREATE TABLE platform_health (
  service       VARCHAR(80)  NOT NULL,
  fallas        INT          NOT NULL DEFAULT 0,
  caido_desde   BIGINT       NULL,
  ultimo_aviso  BIGINT       NULL,
  ultimo_error  VARCHAR(255) NULL,
  updated_at    BIGINT       NULL,
  PRIMARY KEY (service)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- Verificación: deben salir las 6 columnas.
SHOW COLUMNS FROM platform_health;


-- Rollback:
--   DROP TABLE platform_health;
