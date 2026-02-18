import { useMemo, useState } from 'react';
import type { RevenueLine } from '../types';
import styles from './RevenueLinesManager.module.css';

interface RevenueLinesManagerProps {
  lines: RevenueLine[];
  onAdd: (line: RevenueLine) => void;
  onUpdate: (empresa: string, updates: Partial<RevenueLine>) => void;
  onRemove: (empresa: string) => void;
}

export function RevenueLinesManager({
  lines,
  onAdd,
  onUpdate,
  onRemove,
}: RevenueLinesManagerProps) {
  const [name, setName] = useState('');
  const [grupo, setGrupo] = useState('');
  const [segmento, setSegmento] = useState('');
  const [error, setError] = useState('');

  const existingNames = useMemo(() => new Set(lines.map((l) => l.empresa)), [lines]);

  const handleAdd = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedName = name.trim();
    const trimmedGrupo = grupo.trim();
    const trimmedSegmento = segmento.trim();
    if (!trimmedName || !trimmedGrupo || !trimmedSegmento) {
      setError('Preencha linha, grupo e segmento.');
      return;
    }
    if (existingNames.has(trimmedName)) {
      setError('Essa linha já existe.');
      return;
    }
    onAdd({ empresa: trimmedName, grupo: trimmedGrupo, segmento: trimmedSegmento });
    setName('');
    setGrupo('');
    setSegmento('');
    setError('');
  };

  const handleRemove = (empresa: string) => {
    const ok = window.confirm(`Remover a linha "${empresa}"?`);
    if (!ok) return;
    onRemove(empresa);
  };

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <div>
          <h2 className={styles.title}>Linhas de Receita</h2>
          <p className={styles.subtitle}>
            Adicione ou remova linhas. Novas linhas entram automaticamente nos filtros e metas.
          </p>
        </div>
      </div>

      <form className={styles.form} onSubmit={handleAdd}>
        <input
          className={styles.input}
          placeholder="Linha"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (error) setError('');
          }}
        />
        <input
          className={styles.input}
          placeholder="Grupo"
          value={grupo}
          onChange={(e) => {
            setGrupo(e.target.value);
            if (error) setError('');
          }}
        />
        <input
          className={styles.input}
          placeholder="Segmento"
          value={segmento}
          onChange={(e) => {
            setSegmento(e.target.value);
            if (error) setError('');
          }}
        />
        <button type="submit" className={styles.addButton}>
          Adicionar
        </button>
      </form>
      {error && <div className={styles.error}>{error}</div>}

      <div className={styles.list}>
        <div className={styles.listHeader}>
          <span>Linha</span>
          <span>Grupo</span>
          <span>Segmento</span>
          <span />
        </div>
        {lines.map((line) => (
          <div key={line.empresa} className={styles.row}>
            <span className={styles.lineName}>{line.empresa}</span>
            <input
              className={styles.inlineInput}
              value={line.grupo}
              onChange={(e) => onUpdate(line.empresa, { grupo: e.target.value })}
            />
            <input
              className={styles.inlineInput}
              value={line.segmento}
              onChange={(e) => onUpdate(line.empresa, { segmento: e.target.value })}
            />
            <button
              type="button"
              className={styles.removeButton}
              onClick={() => handleRemove(line.empresa)}
            >
              Remover
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
