module aes_mixcolumns (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [127:0] in_state,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [127:0] out_state
);

    logic busy;
    wire  input_accepted;
    wire  output_consumed;

    function automatic logic [7:0] xtime(input logic [7:0] x);
        logic [7:0] shifted;
        begin
            shifted = {x[6:0], 1'b0};
            if (x[7]) begin
                xtime = shifted ^ 8'h1B;
            end else begin
                xtime = shifted;
            end
        end
    endfunction

    function automatic logic [7:0] mul2(input logic [7:0] x);
        begin
            mul2 = xtime(x);
        end
    endfunction

    function automatic logic [7:0] mul3(input logic [7:0] x);
        begin
            mul3 = xtime(x) ^ x;
        end
    endfunction

    function automatic logic [127:0] mixcolumns_fn(input logic [127:0] s);
        logic [7:0] b0,  b1,  b2,  b3;
        logic [7:0] b4,  b5,  b6,  b7;
        logic [7:0] b8,  b9,  b10, b11;
        logic [7:0] b12, b13, b14, b15;
        logic [7:0] o0,  o1,  o2,  o3;
        logic [7:0] o4,  o5,  o6,  o7;
        logic [7:0] o8,  o9,  o10, o11;
        logic [7:0] o12, o13, o14, o15;
        begin
            b0  = s[127:120];
            b1  = s[119:112];
            b2  = s[111:104];
            b3  = s[103:96];
            b4  = s[95:88];
            b5  = s[87:80];
            b6  = s[79:72];
            b7  = s[71:64];
            b8  = s[63:56];
            b9  = s[55:48];
            b10 = s[47:40];
            b11 = s[39:32];
            b12 = s[31:24];
            b13 = s[23:16];
            b14 = s[15:8];
            b15 = s[7:0];

            o0  = mul2(b0)  ^ mul3(b1)  ^ b2       ^ b3;
            o1  = b0        ^ mul2(b1)  ^ mul3(b2) ^ b3;
            o2  = b0        ^ b1        ^ mul2(b2) ^ mul3(b3);
            o3  = mul3(b0)  ^ b1        ^ b2       ^ mul2(b3);

            o4  = mul2(b4)  ^ mul3(b5)  ^ b6       ^ b7;
            o5  = b4        ^ mul2(b5)  ^ mul3(b6) ^ b7;
            o6  = b4        ^ b5        ^ mul2(b6) ^ mul3(b7);
            o7  = mul3(b4)  ^ b5        ^ b6       ^ mul2(b7);

            o8  = mul2(b8)  ^ mul3(b9)  ^ b10      ^ b11;
            o9  = b8        ^ mul2(b9)  ^ mul3(b10)^ b11;
            o10 = b8        ^ b9        ^ mul2(b10)^ mul3(b11);
            o11 = mul3(b8)  ^ b9        ^ b10      ^ mul2(b11);

            o12 = mul2(b12) ^ mul3(b13) ^ b14      ^ b15;
            o13 = b12       ^ mul2(b13) ^ mul3(b14)^ b15;
            o14 = b12       ^ b13       ^ mul2(b14)^ mul3(b15);
            o15 = mul3(b12) ^ b13       ^ b14      ^ mul2(b15);

            mixcolumns_fn = {
                o0,  o1,  o2,  o3,
                o4,  o5,  o6,  o7,
                o8,  o9,  o10, o11,
                o12, o13, o14, o15
            };
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy      <= 1'b0;
            out_state <= 128'h00000000000000000000000000000000;
        end else if (input_accepted) begin
            busy      <= 1'b1;
            out_state <= mixcolumns_fn(in_state);
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule