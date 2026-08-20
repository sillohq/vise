/**
 * The main table, and the side rail beside it.
 *
 * Cells arrive as strings, already formatted by the panel that produced them —
 * `38ms`, `12.4 kB`, `4d 02h`. The interface decides nothing about them.
 *
 * Even the coloured pills are the panel's decision: a cell is drawn as a pill
 * only when its *column* was marked as holding a state. Deciding from the cell's
 * text instead is what the first version did, and it coloured a recorder buffer
 * of `500` as an HTTP server error — because `500` looks like a status code when
 * you have no idea which column you are in.
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
                    const pill =
                      table.columns[index]?.kind === 'state' ? pillFor(cell) : ''

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
