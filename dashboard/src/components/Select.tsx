import { useState, useRef, useEffect } from 'react';
import { ChevronDown, X } from 'lucide-react';
import styles from './Select.module.css';

interface SelectProps {
  label: string;
  value: string | null;
  options: string[];
  onChange: (value: string | null) => void;
  placeholder?: string;
}

export function Select({
  label,
  value,
  options,
  onChange,
  placeholder = 'Todos',
}: SelectProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const filteredOptions = options.filter((opt) =>
    opt.toLowerCase().includes(search.toLowerCase())
  );

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setIsOpen(false);
        setSearch('');
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isOpen]);

  return (
    <div className={styles.container} ref={ref}>
      <span className={styles.label}>{label}</span>
      <button
        type="button"
        className={styles.trigger}
        onClick={() => setIsOpen(!isOpen)}
        aria-expanded={isOpen}
      >
        <span className={value ? styles.value : styles.placeholder}>
          {value || placeholder}
        </span>
        {value ? (
          <X
            size={14}
            className={styles.clear}
            onClick={(e) => {
              e.stopPropagation();
              onChange(null);
            }}
          />
        ) : (
          <ChevronDown size={14} className={styles.chevron} />
        )}
      </button>

      {isOpen && (
        <div className={styles.dropdown}>
          <input
            ref={inputRef}
            type="text"
            className={styles.search}
            placeholder="Buscar..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className={styles.options}>
            {filteredOptions.length === 0 ? (
              <div className={styles.empty}>Nenhum resultado</div>
            ) : (
              filteredOptions.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  className={`${styles.option} ${
                    opt === value ? styles.selected : ''
                  }`}
                  onClick={() => {
                    onChange(opt);
                    setIsOpen(false);
                    setSearch('');
                  }}
                >
                  {opt}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
