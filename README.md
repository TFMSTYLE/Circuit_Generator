# Circuit Generator

![circuit-thumb](https://github.com/user-attachments/assets/421558b9-6098-48b8-b966-438b6be67db9)

**Author:** The French Monkey (TFMSTYLE)  
**Version:** 1.0.0  

---

## Overview

The Circuit Generator creates procedural PCB-style mesh patterns by automatically routing traces between random connection points.  
It simulates an autorouted printed circuit board layout that can be used for design, concept, or visual effects.  
The system places nodes (pads) on a square area and connects them using pathfinding logic to form realistic wiring networks.

---

## Parameters

### Live Update
When enabled, the circuit automatically regenerates whenever a parameter changes.  
This provides immediate feedback but can slow performance on large boards.

---

### Preset
Defines the base scale and complexity of the generated circuit.  
Available options:

- **Custom** – Manual settings, no preset adjustments.  
- **Arduino Uno** – Medium-sized layout with balanced routing.  
- **Raspberry Pi** – Large, dense circuit layout.  
- **Small MCU** – Compact, light layout.  
- **Breakout Board** – Simple, minimal board for smaller modules.  
- **Large Board** – Expansive and densely packed board for high complexity.

Each preset automatically adjusts the area size, node count, and density.

---

### Layout (Density)
Sets the general density of the routing.

- **Sparse** – Open layout with fewer and longer traces.  
- **Balanced** – Standard layout with moderate coverage and even spacing.

---

### Nodes
Number of connection points that the autorouter will create.  
A higher node count increases the complexity and number of traces.

---

### Trace Width
Base width of each circuit trace, in Blender units.  
Controls how visually thick the wiring appears.  

---

### Board Area
Overall size of the circuit board in scene units.  
A larger value produces a wider routing area and longer traces.  

---

### Max Trace Length
Limits the maximum distance a trace can travel while connecting nodes.  
Higher values allow more winding, organic routes; lower values create tighter layouts.  

---

### Line Spacing
Controls the separation distance between traces.  
Smaller values increase the density of routes; larger values reduce overlap and visual clutter.  

---

### Cluster Amount
Probability of traces forming parallel groups or clusters.  
A higher value creates more multi-line bundles that mimic bus wiring.  

---

### Seed
Random seed used for reproducibility.  
Changing the seed generates a different layout while keeping other parameters identical.  

---

### Parallel Routing
Enables multithreaded pathfinding to speed up generation on larger boards.  
This option is recommended for high node counts and dense layouts.  
For smaller circuits, it can be disabled to ensure consistent results.

---

## Operators

### Generate Circuit
Creates a new circuit object or updates the selected one using the current parameters.

---

## Usage Notes

- Decreasing **Line Spacing** or increasing **Nodes** makes the pattern denser.  
- Increasing **Max Trace Length** and **Cluster Amount** produces more intricate designs.  
- For performance, disable **Live Update** when working with very high node counts.  
- The generated mesh can be used as geometry, converted to curves, or used for texture baking.  
- Applying materials, solidify, or wireframe modifiers can create more stylized PCB or sci-fi panel effects.


