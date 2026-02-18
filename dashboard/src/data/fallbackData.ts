import type { FaturamentoRecord } from '../types';

// Company definitions with group and segment
const COMPANIES = [
  // NETAIR
  { empresa: 'NETAIR', grupo: 'NETAIR', segmento: 'AR CONDICIONADO' },
  { empresa: 'NETPARTS', grupo: 'NETAIR', segmento: 'AR CONDICIONADO' },
  { empresa: '141AIR', grupo: 'NETAIR', segmento: 'AR CONDICIONADO' },
  { empresa: 'SHOPEE NETAIR', grupo: 'NETAIR', segmento: 'AR CONDICIONADO' },
  { empresa: 'VITAO', grupo: 'NETAIR', segmento: 'AR CONDICIONADO' },
  { empresa: 'VINICIUS', grupo: 'NETAIR', segmento: 'AR CONDICIONADO' },
  { empresa: 'ARTHUR', grupo: 'NETAIR', segmento: 'AR CONDICIONADO' },
  { empresa: 'JONATHAN', grupo: 'NETAIR', segmento: 'AR CONDICIONADO' },

  // ACA
  { empresa: 'AUTOFY (CONDENSADORES )', grupo: 'ACA', segmento: 'AR CONDICIONADO' },
  { empresa: 'AUTOMY', grupo: 'ACA', segmento: 'AR CONDICIONADO' },
  { empresa: 'SHOPEE ACA', grupo: 'ACA', segmento: 'AR CONDICIONADO' },

  // EASY
  { empresa: 'EASYPEASY SP', grupo: 'EASY', segmento: 'UTILIDADES' },
  { empresa: 'EASYPEASY CWB', grupo: 'EASY', segmento: 'UTILIDADES' },
  { empresa: 'SHOPEE EASY', grupo: 'EASY', segmento: 'UTILIDADES' },

  // BELLATOR
  { empresa: 'BELLATOR CWB', grupo: 'BELLATOR', segmento: 'BALESTRA' },
  { empresa: 'BELLATOR - JUNIOR', grupo: 'BELLATOR', segmento: 'BALESTRA' },
  { empresa: 'BELLATOR - SITE', grupo: 'BELLATOR', segmento: 'BALESTRA' },

  // UNIQUE
  { empresa: 'ML 1 - UNIQUE', grupo: 'UNIQUE', segmento: 'PRESENTES' },
  { empresa: 'ML 2 - UNIQUE', grupo: 'UNIQUE', segmento: 'PRESENTES' },
  { empresa: 'UNIQUEKIDS', grupo: 'UNIQUE', segmento: 'PRESENTES' },
  { empresa: 'UNIQUEBOX', grupo: 'UNIQUE', segmento: 'PRESENTES' },
  { empresa: 'MANU', grupo: 'UNIQUE', segmento: 'PRESENTES' },
  { empresa: 'REPRESENTANTES', grupo: 'UNIQUE', segmento: 'PRESENTES' },
  { empresa: 'SITE TERCEIROS', grupo: 'UNIQUE', segmento: 'PRESENTES' },
];

export function getFallbackData(): FaturamentoRecord[] {
  return [];
}

// Export company list for use in other modules
export { COMPANIES };
