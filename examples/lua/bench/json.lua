-- A small JSON-like encoder over nested tables: type dispatch, escaping, recursion (placemat examples, MIT).
local escapes = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\t'] = '\\t' }
local encode
local function encode_string(s) return '"' .. s:gsub('[%c"\\]', function(c) return escapes[c] or ("\\u%04x"):format(c:byte()) end) .. '"' end
function encode(v, out)
  local t = type(v)
  if t == "table" then
    if #v > 0 then
      out[#out + 1] = "["
      for i = 1, #v do
        if i > 1 then out[#out + 1] = "," end
        encode(v[i], out)
      end
      out[#out + 1] = "]"
    else
      local keys = {}
      for k in pairs(v) do keys[#keys + 1] = k end
      table.sort(keys)
      out[#out + 1] = "{"
      for i, k in ipairs(keys) do
        if i > 1 then out[#out + 1] = "," end
        out[#out + 1] = encode_string(k)
        out[#out + 1] = ":"
        encode(v[k], out)
      end
      out[#out + 1] = "}"
    end
  elseif t == "string" then out[#out + 1] = encode_string(v)
  elseif t == "number" then out[#out + 1] = tostring(v)
  elseif t == "boolean" then out[#out + 1] = v and "true" or "false"
  else out[#out + 1] = "null" end
end
local doc = {}
for i = 1, 400 do
  doc[i] = { id = i, name = "user\t" .. i, score = i * 1.5, tags = { "a", "b\"q", "c" .. i }, ok = i % 2 == 0,
             nested = { depth = { value = i, list = { 1, 2, 3, i } } } }
end
return { n = 24, run = function(n)
  local total = 0
  for _ = 1, n do
    local out = {}
    encode(doc, out)
    total = total + #table.concat(out)
  end
  return total
end }
