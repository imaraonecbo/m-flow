import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

export class JsonStore {
  constructor(file) {
    this.file = file;
    fs.mkdirSync(path.dirname(file), { recursive: true });
    if (!fs.existsSync(file)) fs.writeFileSync(file, '[]\n', 'utf8');
  }
  all() {
    try {
      const parsed = JSON.parse(fs.readFileSync(this.file, 'utf8') || '[]');
      if (!Array.isArray(parsed)) throw new Error('Store root must be an array');
      return parsed;
    } catch (error) {
      throw new Error(`Corrupt store ${this.file}: ${error.message}`);
    }
  }
  get(id) { return this.all().find((x) => x.id === id) ?? null; }
  insert(value) {
    const rows = this.all();
    const id = value.id ?? randomUUID();
    if (rows.some((x) => x.id === id)) throw new Error(`Duplicate store id: ${id}`);
    const row = { id, createdAt: new Date().toISOString(), ...value };
    rows.push(row);
    this.#write(rows);
    return row;
  }
  update(id, patch) {
    const rows = this.all();
    const index = rows.findIndex((x) => x.id === id);
    if (index < 0) return null;
    rows[index] = { ...rows[index], ...patch, updatedAt: new Date().toISOString() };
    this.#write(rows);
    return rows[index];
  }
  #write(rows) {
    const tmp = `${this.file}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(rows, null, 2) + '\n', 'utf8');
    fs.renameSync(tmp, this.file);
  }
}
