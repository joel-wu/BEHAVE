
module TopModule (
  input x,
  input y,
  output z
);
  wire a1,a2,b1,b2;
  LogicA aa1(x,y,a1), aa2(x,y,a2);
  LogicB bb1(x,y,b1), bb2(x,y,b2);
  assign z = (a1 | b1) ^ (a2 & b2);
endmodule
module LogicA(input x,y, output z);
  assign z=(x^y)&x;
endmodule
module LogicB(input x,y, output z);
  assign z=~(x^y);
endmodule
