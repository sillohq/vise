/**
 * The main table, and the side rail beside it.
 *
 * Cells arrive as strings, already formatted by the panel that produced them —
 * `38ms`, `12.4 kB`, `4d 02h`. The interface decides one thing about a cell:
 * whether it is a state worth a coloured pill, which `pillFor` answers from the
 * framework's own vocabulary.
 */

import type { Aside, Table as TableData } from '../types'
import { columnClass, dotFor, pillFor } from '../tone'

export function Table({ table, empty }: { table: TableData; empty: string }) {
  const classes = table.columns.map(column => columnClass(column.cls))

  return (
    <div className="table">
      {table.rows.length === 0 ? (
        <div className="table__empty">{empty}</div>
      ) : (
        <div className="table__scroll">
          <table>
            <thead>
              <tr>
                {table.columns.map((column, index) => (
                  <th key={column.label} className={classes[index]}>
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, rowIndex) => (
                <tr key={`${rowIndex}-${row[0] ?? ''}`}>
                  {row.map((cell, index) => {
                    const pill = pillFor(cell)
                    return (
                      <td key={index} className={classes[index]} title={cell}>
                        {pill ? <span className={pill}>{cell}</span> : cell}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export function Rail({ aside }: { aside: Aside }) {
  return (
    <div className="rail">
      <div className="rail__head">
        <span className="rail__label">{aside.label}</span>
        <span className="rail__note">{aside.note}</span>
      </div>
      {aside.rows.map(([name, detail, state]) => (
        <div className="rail__row" key={name}>
          {state ? <span className={dotFor(state)} /> : null}
          <div className="rail__body">
            <div className="rail__name" title={name}>
              {name}
            </div>
            {state ? (
              <div className="rail__detail" title={detail}>
                {detail}
              </div>
            ) : null}
          </div>
          <span className="rail__note">{state ?? detail}</span>
        </div>
      ))}
    </div>
  )
}
