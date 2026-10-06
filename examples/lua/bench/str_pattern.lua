-- Lua patterns: gsub, find and gmatch over a generated text (placemat examples, MIT).
local words = {}
for i = 1, 2000 do words[i] = ("w%d_%s"):format(i, ("abcdefgh"):sub(1 + i % 8)) end
local text = table.concat(words, " ")
return { n = 48, run = function(n)
  local total = 0
  for _ = 1, n do
    local s, c = text:gsub("(%a+)_(%a+)", "%2-%1")
    total = total + c + #s
    for w in text:gmatch("w(%d+)_") do total = total + #w end
    local p = 1
    while true do
      local a, b = text:find("_ab", p, true)
      if not a then break end
      total = total + 1
      p = b + 1
    end
    total = total + (text:upper():len())
  end
  return total
end }
