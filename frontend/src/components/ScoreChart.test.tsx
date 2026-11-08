import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ScoreChart, { type ChartPoint } from './ScoreChart'

const points: ChartPoint[] = [
  { version: 2, accuracy: 0.8, grade: 1.5, kept: true },
  { version: 3, accuracy: 0.7, grade: 1.2, kept: false },
  { version: 4, accuracy: 0.92, grade: 2.1, kept: true },
]

describe('ScoreChart', () => {
  it('draws a point for each version tried, in each of the two charts', () => {
    const { container } = render(<ScoreChart points={points} />)
    expect(container.querySelectorAll('svg')).toHaveLength(2)
    expect(container.querySelectorAll('circle.point')).toHaveLength(6)
  })

  it('draws a version that was rolled back hollow, and one that was kept filled', () => {
    const { container } = render(<ScoreChart points={points} />)
    const accuracy = container.querySelector('svg[aria-label="Held-out accuracy of each version tried"]')!
    const circles = [...accuracy.querySelectorAll('circle.point')]
    expect(circles.map((c) => c.getAttribute('data-kept'))).toEqual(['true', 'false', 'true'])
    expect(circles[0]).toHaveClass('point-kept')
    expect(circles[1]).toHaveClass('point-rolled-back')
  })

  it('labels the axes and each version', () => {
    render(<ScoreChart points={points} />)
    expect(screen.getAllByText('v2').length).toBeGreaterThan(0)
    expect(screen.getAllByText('v4').length).toBeGreaterThan(0)
    expect(screen.getByText('100%')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
  })

  it('says what to do when there is nothing to draw', () => {
    render(<ScoreChart points={[]} />)
    expect(screen.getByText(/No version has been tried yet/)).toBeInTheDocument()
  })
})
