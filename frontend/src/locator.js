export function gridCenter(value) {
  const grid=value.trim().toUpperCase()
  if(!/^[A-R]{2}[0-9]{2}(?:[A-X]{2}(?:[0-9]{2})?)?$/.test(grid)) {
    throw new Error('请输入 4、6 或 8 位 Maidenhead 定位，例如 OM20、OM20BO、OM20BO24')
  }
  let longitude=-180+(grid.charCodeAt(0)-65)*20+Number(grid[2])*2
  let latitude=-90+(grid.charCodeAt(1)-65)*10+Number(grid[3])
  let width=2,height=1
  if(grid.length>=6) {
    width/=24; height/=24
    longitude+=(grid.charCodeAt(4)-65)*width
    latitude+=(grid.charCodeAt(5)-65)*height
  }
  if(grid.length===8) {
    width/=10; height/=10
    longitude+=Number(grid[6])*width; latitude+=Number(grid[7])*height
  }
  return {locator:grid,latitude:Number((latitude+height/2).toFixed(8)),longitude:Number((longitude+width/2).toFixed(8))}
}
