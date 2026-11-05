module nuttall3_window_sample (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [5:0]  in_block_size,
    input  logic [5:0]  in_index,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_value
);

logic busy;
logic [31:0] lut_value;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    lut_value = 32'h00000000;

    case ({in_block_size, in_index})
        12'h040: lut_value = 32'h00000000;

        12'h080: lut_value = 32'h00000000;
        12'h081: lut_value = 32'h3F800000;

        12'h0C0: lut_value = 32'h00000000;
        12'h0C1: lut_value = 32'h3F100000;
        12'h0C2: lut_value = 32'h3F100000;

        12'h100: lut_value = 32'h00000000;
        12'h101: lut_value = 32'h3E800000;
        12'h102: lut_value = 32'h3F800000;
        12'h103: lut_value = 32'h3E800000;

        12'h140: lut_value = 32'h00000000;
        12'h141: lut_value = 32'h3DF47550;
        12'h142: lut_value = 32'h3F517156;
        12'h143: lut_value = 32'h3F517156;
        12'h144: lut_value = 32'h3DF47550;

        12'h180: lut_value = 32'h00000000;
        12'h181: lut_value = 32'h3D800000;
        12'h182: lut_value = 32'h3F100000;
        12'h183: lut_value = 32'h3F800000;
        12'h184: lut_value = 32'h3F100000;
        12'h185: lut_value = 32'h3D800000;

        12'h1C0: lut_value = 32'h00000000;
        12'h1C1: lut_value = 32'h3D112984;
        12'h1C2: lut_value = 32'h3EBF4DA8;
        12'h1C3: lut_value = 32'h3F674694;
        12'h1C4: lut_value = 32'h3F674694;
        12'h1C5: lut_value = 32'h3EBF4DA8;
        12'h1C6: lut_value = 32'h3D112984;

        12'h200: lut_value = 32'h00000000;
        12'h201: lut_value = 32'h3CAFB0CD;
        12'h202: lut_value = 32'h3E800000;
        12'h203: lut_value = 32'h3F3A827A;
        12'h204: lut_value = 32'h3F800000;
        12'h205: lut_value = 32'h3F3A827A;
        12'h206: lut_value = 32'h3E800000;
        12'h207: lut_value = 32'h3CAFB0CD;

        12'h240: lut_value = 32'h00000000;
        12'h241: lut_value = 32'h3C603205;
        12'h242: lut_value = 32'h3E2ECFBD;
        12'h243: lut_value = 32'h3F100000;
        12'h244: lut_value = 32'h3F70CB49;
        12'h245: lut_value = 32'h3F70CB49;
        12'h246: lut_value = 32'h3F100000;
        12'h247: lut_value = 32'h3E2ECFBD;
        12'h248: lut_value = 32'h3C603205;

        12'h280: lut_value = 32'h00000000;
        12'h281: lut_value = 32'h3C15664B;
        12'h282: lut_value = 32'h3DF47550;
        12'h283: lut_value = 32'h3EDB54CE;
        12'h284: lut_value = 32'h3F517156;
        12'h285: lut_value = 32'h3F800000;
        12'h286: lut_value = 32'h3F517156;
        12'h287: lut_value = 32'h3EDB54CE;
        12'h288: lut_value = 32'h3DF47550;
        12'h289: lut_value = 32'h3C15664B;

        12'h2C0: lut_value = 32'h00000000;
        12'h2C1: lut_value = 32'h3BCE7128;
        12'h2C2: lut_value = 32'h3DAEF87E;
        12'h2C3: lut_value = 32'h3EA7066A;
        12'h2C4: lut_value = 32'h3F2F44A2;
        12'h2C5: lut_value = 32'h3F75BC37;
        12'h2C6: lut_value = 32'h3F75BC37;
        12'h2C7: lut_value = 32'h3F2F44A2;
        12'h2C8: lut_value = 32'h3EA7066A;
        12'h2C9: lut_value = 32'h3DAEF87E;
        12'h2CA: lut_value = 32'h3BCE7128;

        12'h300: lut_value = 32'h00000000;
        12'h301: lut_value = 32'h3B930A2F;
        12'h302: lut_value = 32'h3D800000;
        12'h303: lut_value = 32'h3E800000;
        12'h304: lut_value = 32'h3F100000;
        12'h305: lut_value = 32'h3F5ED9EC;
        12'h306: lut_value = 32'h3F800000;
        12'h307: lut_value = 32'h3F5ED9EC;
        12'h308: lut_value = 32'h3F100000;
        12'h309: lut_value = 32'h3E800000;
        12'h30A: lut_value = 32'h3D800000;
        12'h30B: lut_value = 32'h3B930A2F;

        12'h340: lut_value = 32'h00000000;
        12'h341: lut_value = 32'h3B56F69F;
        12'h342: lut_value = 32'h3D3F0BB3;
        12'h343: lut_value = 32'h3E460132;
        12'h344: lut_value = 32'h3EEADFC9;
        12'h345: lut_value = 32'h3F43AAA3;
        12'h346: lut_value = 32'h3F789D7A;
        12'h347: lut_value = 32'h3F789D7A;
        12'h348: lut_value = 32'h3F43AAA3;
        12'h349: lut_value = 32'h3EEADFC9;
        12'h34A: lut_value = 32'h3E460132;
        12'h34B: lut_value = 32'h3D3F0BB3;
        12'h34C: lut_value = 32'h3B56F69F;

        12'h380: lut_value = 32'h00000000;
        12'h381: lut_value = 32'h3B20AE3B;
        12'h382: lut_value = 32'h3D112984;
        12'h383: lut_value = 32'h3E1ABECA;
        12'h384: lut_value = 32'h3EBF4DA8;
        12'h385: lut_value = 32'h3F28AF9F;
        12'h386: lut_value = 32'h3F674694;
        12'h387: lut_value = 32'h3F800000;
        12'h388: lut_value = 32'h3F674694;
        12'h389: lut_value = 32'h3F28AF9F;
        12'h38A: lut_value = 32'h3EBF4DA8;
        12'h38B: lut_value = 32'h3E1ABECA;
        12'h38C: lut_value = 32'h3D112984;
        12'h38D: lut_value = 32'h3B20AE3B;

        12'h3C0: lut_value = 32'h00000000;
        12'h3C1: lut_value = 32'h3AF4EBB6;
        12'h3C2: lut_value = 32'h3CE03432;
        12'h3C3: lut_value = 32'h3DF47550;
        12'h3C4: lut_value = 32'h3E9C2868;
        12'h3C5: lut_value = 32'h3F100000;
        12'h3C6: lut_value = 32'h3F517156;
        12'h3C7: lut_value = 32'h3F7A6FB4;
        12'h3C8: lut_value = 32'h3F7A6FB4;
        12'h3C9: lut_value = 32'h3F517156;
        12'h3CA: lut_value = 32'h3F100000;
        12'h3CB: lut_value = 32'h3E9C2868;
        12'h3CC: lut_value = 32'h3DF47550;
        12'h3CD: lut_value = 32'h3CE03432;
        12'h3CE: lut_value = 32'h3AF4EBB6;

        12'h400: lut_value = 32'h00000000;
        12'h401: lut_value = 32'h3ABDDE53;
        12'h402: lut_value = 32'h3CAFB0CD;
        12'h403: lut_value = 32'h3DC31CE2;
        12'h404: lut_value = 32'h3E800000;
        12'h405: lut_value = 32'h3EF4B64E;
        12'h406: lut_value = 32'h3F3A827A;
        12'h407: lut_value = 32'h3F6CE24E;
        12'h408: lut_value = 32'h3F800000;
        12'h409: lut_value = 32'h3F6CE24E;
        12'h40A: lut_value = 32'h3F3A827A;
        12'h40B: lut_value = 32'h3EF4B64E;
        12'h40C: lut_value = 32'h3E800000;
        12'h40D: lut_value = 32'h3DC31CE2;
        12'h40E: lut_value = 32'h3CAFB0CD;
        12'h40F: lut_value = 32'h3ABDDE53;
    endcase
end

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy      <= 1'b0;
        out_value <= 32'h00000000;
    end else if (input_accepted) begin
        busy      <= 1'b1;
        out_value <= lut_value;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
