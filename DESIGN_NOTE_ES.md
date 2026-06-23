# Nota de Diseño — Pipeline de Tipos de Cambio
**B2 Impact · Senior BI Analyst · Caso Técnico**

---

## 1. Fuente de datos — Banco Central Europeo (BCE)

**Decisión:** Utilizar el fichero oficial `eurofxref-hist.zip` del BCE como única fuente de datos.

**Justificación:**
- El BCE es la fuente oficial de los tipos de referencia del euro en Europa. Todos los proveedores de terceros (frankfurter.app, exchangeratesapi.io, etc.) derivan sus datos en última instancia de este fichero.
- Una única petición HTTP descarga el histórico completo desde 1999 — sin clave de API, sin límites de llamadas, sin paginación.
- La URL es estable y se actualiza diariamente a las ~16:00 CET en cada día hábil TARGET2.

**Ventajas e inconvenientes:**

| Ventaja | Inconveniente |
|---|---|
| Fuente oficial y auditable | Solo publica tasas con base EUR (los pares cruzados deben derivarse) |
| Gratuita, sin registro | Actualización una vez al día — sin tipos intradía |
| Histórico completo en una sola petición | Sujeta al calendario bancario del BCE (sin tasas en fin de semana) |

---

## 2. Ventana histórica — 5 años

**Decisión:** Cargar datos desde el 1 de enero de cinco años antes de la fecha de ejecución.

**Justificación:**
- Cinco años ofrecen profundidad suficiente para comparaciones YTD entre varios años, análisis de tendencias y períodos de calentamiento de las medias móviles.
- El dataset resultante (~58.800 filas) es lo bastante pequeño para importarse directamente en Power BI sin problemas de rendimiento.

**Ventajas e inconvenientes:**

| Ventaja | Inconveniente |
|---|---|
| Cubre varios ciclos económicos | Excluye el histórico pre-2021 (impacto COVID, pre-Brexit) |
| Ejecución rápida del pipeline (~3 segundos) | `START_DATE` debe actualizarse manualmente para ampliar la ventana |
| Tamaño de fichero manejable (~4 MB) | |

> La constante `START_DATE` en `pipeline.py` puede modificarse en cualquier momento para ampliar o reducir la ventana.

---

## 3. Cálculo de pares cruzados

**Decisión:** Derivar los 42 pares cruzados ordenados aritméticamente a partir de las tasas con base EUR mediante la fórmula:

```
tasa(A → B) = BCE[B] / BCE[A]
```

**Justificación:**
- El BCE publica 1 EUR = X unidades para cada divisa. Al dividir dos tasas se cancela el EUR y se obtiene el tipo cruzado exacto.
- Los tipos derivados aritméticamente son consistentes por construcción — no es posible el arbitraje triangular.
- Evita realizar 42 llamadas independientes a una API y garantiza la coherencia interna entre todos los pares.

**Ventajas e inconvenientes:**

| Ventaja | Inconveniente |
|---|---|
| Fuente única de verdad | Son tipos de referencia, no precios de mercado bid/ask |
| Sin inconsistencias de redondeo entre pares | Los tipos derivados pueden diferir ligeramente de los cruzados de mercado |
| Computacionalmente eficiente | |

---

## 4. Formato de salida y esquema de datos

**Decisión:** Fichero CSV plano en formato largo (tall), con punto y coma como separador de campos y coma como separador decimal.

**Justificación:**
- **Formato largo** — una fila por par y fecha — se integra de forma nativa con el modelo de filtros y segmentaciones de Power BI. Un único slicer sobre `pair_label` filtra todos los visuales simultáneamente sin necesidad de transformaciones en Power Query.
- **Métricas precalculadas** — `daily_change`, `ytd_change_pct`, medias móviles — reducen la complejidad DAX en Power BI y hacen el fichero autocontenido para cualquier herramienta BI.
- **Formato con punto y coma/coma** es compatible con la configuración regional europea de Windows, garantizando la interpretación correcta de los números en Power BI sin configuración adicional.
- **CSV frente a Parquet** — elegido por máxima compatibilidad. Cualquier analista puede abrir e inspeccionar el fichero en Excel sin herramientas adicionales.

**Ventajas e inconvenientes:**

| Ventaja | Inconveniente |
|---|---|
| No se necesitan transformaciones en Power Query | Fichero mayor que Parquet (~4 MB vs ~0,5 MB) |
| Nombres de columna autodocumentados | Las columnas de texto repetidas (pair_label, etc.) aumentan el tamaño |
| Compatible con Excel, Power BI y cualquier herramienta BI | Fichero único — sin particionado para volúmenes muy grandes |

---

## 5. Definición del YTD

**Decisión:** El YTD se define como la variación porcentual desde el **primer día hábil del BCE de cada año natural** para ese par específico.

```
ytd_change_pct = (tasa_hoy - tasa_1ene) / tasa_1ene × 100
```

**Justificación:**
- Se calcula para todos los años del dataset (no solo el año en curso), lo que permite comparaciones interanuales en los slicers de Power BI.
- Usar el primer día de trading en lugar del 31 de diciembre del año anterior evita vacíos cuando el dataset comienza a mediados de enero.

**Ventajas e inconvenientes:**

| Ventaja | Inconveniente |
|---|---|
| Funciona para todos los años del dataset | El primer día hábil varía por año (no siempre es el 1 de enero) |
| Coherente con la definición financiera estándar de YTD | Pares con datos iniciales incompletos (p. ej. RON antes de 2005) tienen una base YTD más tardía |

---

## 6. Selección de métricas

| Métrica | Definición | Propósito |
|---|---|---|
| `daily_change` | tasa(t) − tasa(t−1) | Variación diaria absoluta |
| `daily_change_pct` | daily_change / tasa(t−1) × 100 | Variación diaria normalizada |
| `rate_7d_avg` | Media móvil 7 días | Suavizado de tendencia a corto plazo |
| `rate_30d_avg` | Media móvil 30 días | Línea base de tendencia a medio plazo |
| `ytd_change_pct` | % de variación desde el 1 de enero | Rendimiento acumulado en el año |
| `trend_signal` (DAX) | Media 7d vs media 30d | Indicador de momentum (Alcista/Bajista/Neutral) |

**Nota:** Las medias móviles usan `min_periods=1` para no descartar los primeros días de la serie. Esto significa que las primeras medias se calculan con menos de 7 o 30 observaciones, lo cual es aceptable dado que el histórico de 5 años proporciona un período de calentamiento suficiente.

---

## 7. Compatibilidad de separadores numéricos — formato americano vs. formato español

**Problema encontrado:** Python y pandas generan ficheros CSV usando el **formato numérico americano** por defecto: punto (`.`) como separador decimal y coma (`,`) como separador de miles. Power BI Desktop configurado con la **región española o europea** lee estos ficheros con la convención contraria — el punto como separador de miles y la coma como decimal — provocando una interpretación errónea sistemática de todos los valores numéricos.

**Ejemplo concreto:**

| Valor en el CSV (Python por defecto) | Power BI lo lee (con configuración española) |
|---|---|
| `7.474700` | 7.474.700 — error de un factor de 1.000.000 |
| `-2.6E-05` | `-2,6E-05` en notación científica |
| `0.317000` | 317.000 — completamente incorrecto |

El primer síntoma detectado fue la tarjeta `Latest Rate` para EUR/DKK mostrando **7.474.700** en lugar de **7,4747**. El segundo síntoma fue la aparición de valores como **-2,6E-05** (notación científica) en la previsualización de datos de Power BI para columnas como `daily_change`.

**Causa raíz — dos problemas independientes:**

1. **Conflicto de separador decimal:** pandas escribe `.` como decimal; Power BI en español espera `,`.
2. **Notación científica:** pandas cambia automáticamente a notación científica para valores muy pequeños (p. ej. `9e-05`). Combinado con el conflicto de separadores, el valor resulta ilegible en Power BI.

**Solución aplicada:** Se sustituyó la llamada directa a `to_csv()` por un paso de preformateo explícito antes de la escritura:

```python
# Formateo explícito: 6 decimales fijos, coma como separador decimal
for col in float_cols:
    output_df[col] = output_df[col].apply(
        lambda x: f"{x:.6f}".replace(".", ",") if pd.notna(x) else ""
    )
output_df.to_csv(OUTPUT_FILE, index=False, date_format="%Y-%m-%d", sep=";")
```

Este enfoque:
- Fuerza **notación de punto fijo** (`%.6f`) — elimina completamente la notación científica
- Reemplaza `.` por `,` — separador decimal correcto para la configuración española
- Usa `;` como separador de campos — estándar en ficheros CSV europeos donde la `,` está reservada para decimales
- Power BI con configuración regional española lee el fichero de forma nativa sin ninguna transformación en Power Query

**¿Por qué no usar `to_csv(decimal=",", float_format="%.6f")`?**

Esta opción de pandas se probó en primera instancia pero resultó poco fiable: pandas aplica el formato de cadena antes de la sustitución del decimal, y para ciertos valores float la sustitución no se propagaba correctamente, dejando notación científica residual en el fichero. El preformateo explícito es más robusto y transparente.

**Ventajas e inconvenientes:**

| Ventaja | Inconveniente |
|---|---|
| Sin configuración adicional en Power BI | El fichero no es legible directamente por sistemas con configuración americana |
| Sin notación científica en ninguna celda | Requiere un bucle de formateo explícito en el pipeline |
| Totalmente compatible con Excel y Power BI en español | Si se comparte internacionalmente, debe documentarse la convención de separadores |

> **Nota para uso internacional:** si el fichero necesita ser consumido por un sistema con configuración americana, basta con eliminar el `.replace(".", ",")` y cambiar `sep=";"` de nuevo a `sep=","`.

---

## 8. Modelo de datos en Power BI

**Decisión:** Modelo de tabla plana única — sin esquema en estrella.

**Justificación:**
- Con una única tabla de hechos y sin tablas de dimensiones, el modelo es sencillo de mantener y comprensible de inmediato por cualquier analista.
- Todas las dimensiones temporales (`year`, `month`, `quarter`, `week`) están precalculadas en el pipeline, eliminando la necesidad de una tabla de fechas separada para el análisis básico.
- Las medidas DAX se almacenan en una tabla aislada `_Medidas` para que sobrevivan a las actualizaciones de la fuente de datos sin perderse.

**Ventajas e inconvenientes:**

| Ventaja | Inconveniente |
|---|---|
| Complejidad de modelo nula | Sin tabla de fechas reutilizable (limita DAX de inteligencia de tiempo avanzado) |
| Rápido de configurar y transferir | Las columnas de dimensión repetidas aumentan el tamaño del modelo |
| Las medidas sobreviven a las actualizaciones de datos | Menos flexible para uniones multi-tabla si el alcance se amplía |

---

*Pipeline ejecutado el: 23/06/2026 · Datos hasta: 22/06/2026 · Fuente: ECB eurofxref-hist.zip*
