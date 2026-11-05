module hft144d_window_coefficient (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_index,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_coeff
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [31:0] coeff_lut(input logic [7:0] idx);
    begin
        unique case (idx)
            8'h00: coeff_lut = 32'h00000000;
            8'h01: coeff_lut = 32'hB579E2A7;
            8'h02: coeff_lut = 32'hB6818BC5;
            8'h03: coeff_lut = 32'hB71A7245;
            8'h04: coeff_lut = 32'hB7944341;
            8'h05: coeff_lut = 32'hB7FE172A;
            8'h06: coeff_lut = 32'hB84B18F4;
            8'h07: coeff_lut = 32'hB89AD333;
            8'h08: coeff_lut = 32'hB8E3F0F2;
            8'h09: coeff_lut = 32'hB9234187;
            8'h0A: coeff_lut = 32'hB964A754;
            8'h0B: coeff_lut = 32'hB99D1965;
            8'h0C: coeff_lut = 32'hB9D4538B;
            8'h0D: coeff_lut = 32'hBA0D65B2;
            8'h0E: coeff_lut = 32'hBA39DE7E;
            8'h0F: coeff_lut = 32'hBA717137;
            8'h10: coeff_lut = 32'hBA9B1FD7;
            8'h11: coeff_lut = 32'hBAC55B12;
            8'h12: coeff_lut = 32'hBAF8C861;
            8'h13: coeff_lut = 32'hBB1B7833;
            8'h14: coeff_lut = 32'hBB40C459;
            8'h15: coeff_lut = 32'hBB6D3AE8;
            8'h16: coeff_lut = 32'hBB90F33F;
            8'h17: coeff_lut = 32'hBBAFF555;
            8'h18: coeff_lut = 32'hBBD44222;
            8'h19: coeff_lut = 32'hBBFE84B5;
            8'h1A: coeff_lut = 32'hBC17BA48;
            8'h1B: coeff_lut = 32'hBC33EAA3;
            8'h1C: coeff_lut = 32'hBC543AE8;
            8'h1D: coeff_lut = 32'hBC7917DB;
            8'h1E: coeff_lut = 32'hBC917974;
            8'h1F: coeff_lut = 32'hBCA920B6;
            8'h20: coeff_lut = 32'hBCC3BDE6;
            8'h21: coeff_lut = 32'hBCE18E30;
            8'h22: coeff_lut = 32'hBD01678E;
            8'h23: coeff_lut = 32'hBD13DEEA;
            8'h24: coeff_lut = 32'hBD284B27;
            8'h25: coeff_lut = 32'hBD3EC93A;
            8'h26: coeff_lut = 32'hBD577487;
            8'h27: coeff_lut = 32'hBD72665E;
            8'h28: coeff_lut = 32'hBD87DAAE;
            8'h29: coeff_lut = 32'hBD97BA66;
            8'h2A: coeff_lut = 32'hBDA8D9FD;
            8'h2B: coeff_lut = 32'hBDBB3EBD;
            8'h2C: coeff_lut = 32'hBDCEEB35;
            8'h2D: coeff_lut = 32'hBDE3DED5;
            8'h2E: coeff_lut = 32'hBDFA158E;
            8'h2F: coeff_lut = 32'hBE08C3B2;
            8'h30: coeff_lut = 32'hBE151405;
            8'h31: coeff_lut = 32'hBE21F33E;
            8'h32: coeff_lut = 32'hBE2F564D;
            8'h33: coeff_lut = 32'hBE3D2F60;
            8'h34: coeff_lut = 32'hBE4B6DBA;
            8'h35: coeff_lut = 32'hBE59FD80;
            8'h36: coeff_lut = 32'hBE68C796;
            8'h37: coeff_lut = 32'hBE77B179;
            8'h38: coeff_lut = 32'hBE834E91;
            8'h39: coeff_lut = 32'hBE8AB476;
            8'h3A: coeff_lut = 32'hBE91F7C2;
            8'h3B: coeff_lut = 32'hBE9903EC;
            8'h3C: coeff_lut = 32'hBE9FC291;
            8'h3D: coeff_lut = 32'hBEA61B70;
            8'h3E: coeff_lut = 32'hBEABF47A;
            8'h3F: coeff_lut = 32'hBEB131D6;
            8'h40: coeff_lut = 32'hBEB5B5FD;
            8'h41: coeff_lut = 32'hBEB961CB;
            8'h42: coeff_lut = 32'hBEBC149F;
            8'h43: coeff_lut = 32'hBEBDAC80;
            8'h44: coeff_lut = 32'hBEBE0643;
            8'h45: coeff_lut = 32'hBEBCFDBC;
            8'h46: coeff_lut = 32'hBEBA6DF0;
            8'h47: coeff_lut = 32'hBEB63152;
            8'h48: coeff_lut = 32'hBEB02202;
            8'h49: coeff_lut = 32'hBEA81A0F;
            8'h4A: coeff_lut = 32'hBE9DF3C0;
            8'h4B: coeff_lut = 32'hBE9189E3;
            8'h4C: coeff_lut = 32'hBE82B81F;
            8'h4D: coeff_lut = 32'hBE62B685;
            8'h4E: coeff_lut = 32'hBE3AA340;
            8'h4F: coeff_lut = 32'hBE0CF6C6;
            8'h50: coeff_lut = 32'hBDB2EBA8;
            8'h51: coeff_lut = 32'hBCFF5213;
            8'h52: coeff_lut = 32'h3CFEE250;
            8'h53: coeff_lut = 32'h3DCC0F61;
            8'h54: coeff_lut = 32'h3E32BAA9;
            8'h55: coeff_lut = 32'h3E83080F;
            8'h56: coeff_lut = 32'h3EB00D48;
            8'h57: coeff_lut = 32'h3EE071C4;
            8'h58: coeff_lut = 32'h3F0A1AC2;
            8'h59: coeff_lut = 32'h3F25A9C5;
            8'h5A: coeff_lut = 32'h3F42E0D4;
            8'h5B: coeff_lut = 32'h3F61B826;
            8'h5C: coeff_lut = 32'h3F81129A;
            8'h5D: coeff_lut = 32'h3F920D51;
            8'h5E: coeff_lut = 32'h3FA3C422;
            8'h5F: coeff_lut = 32'h3FB62D8A;
            8'h60: coeff_lut = 32'h3FC93E9B;
            8'h61: coeff_lut = 32'h3FDCEB02;
            8'h62: coeff_lut = 32'h3FF1250C;
            8'h63: coeff_lut = 32'h4002EEDB;
            8'h64: coeff_lut = 32'h400D825B;
            8'h65: coeff_lut = 32'h40184445;
            8'h66: coeff_lut = 32'h40232B4B;
            8'h67: coeff_lut = 32'h402E2D95;
            8'h68: coeff_lut = 32'h403940D7;
            8'h69: coeff_lut = 32'h40445A55;
            8'h6A: coeff_lut = 32'h404F6EFD;
            8'h6B: coeff_lut = 32'h405A736B;
            8'h6C: coeff_lut = 32'h40655C06;
            8'h6D: coeff_lut = 32'h40701D08;
            8'h6E: coeff_lut = 32'h407AAA99;
            8'h6F: coeff_lut = 32'h40827C6D;
            8'h70: coeff_lut = 32'h40877E01;
            8'h71: coeff_lut = 32'h408C5434;
            8'h72: coeff_lut = 32'h4090F94F;
            8'h73: coeff_lut = 32'h409567C3;
            8'h74: coeff_lut = 32'h40999A2E;
            8'h75: coeff_lut = 32'h409D8B6A;
            8'h76: coeff_lut = 32'h40A13694;
            8'h77: coeff_lut = 32'h40A49713;
            8'h78: coeff_lut = 32'h40A7A8A5;
            8'h79: coeff_lut = 32'h40AA6762;
            8'h7A: coeff_lut = 32'h40ACCFC7;
            8'h7B: coeff_lut = 32'h40AEDEBA;
            8'h7C: coeff_lut = 32'h40B09192;
            8'h7D: coeff_lut = 32'h40B1E61A;
            8'h7E: coeff_lut = 32'h40B2DA97;
            8'h7F: coeff_lut = 32'h40B36DC8;
            8'h80: coeff_lut = 32'h40B39EEE;
            8'h81: coeff_lut = 32'h40B36DC8;
            8'h82: coeff_lut = 32'h40B2DA97;
            8'h83: coeff_lut = 32'h40B1E61A;
            8'h84: coeff_lut = 32'h40B09192;
            8'h85: coeff_lut = 32'h40AEDEBA;
            8'h86: coeff_lut = 32'h40ACCFC7;
            8'h87: coeff_lut = 32'h40AA6762;
            8'h88: coeff_lut = 32'h40A7A8A5;
            8'h89: coeff_lut = 32'h40A49713;
            8'h8A: coeff_lut = 32'h40A13694;
            8'h8B: coeff_lut = 32'h409D8B6A;
            8'h8C: coeff_lut = 32'h40999A2E;
            8'h8D: coeff_lut = 32'h409567C3;
            8'h8E: coeff_lut = 32'h4090F94F;
            8'h8F: coeff_lut = 32'h408C5434;
            8'h90: coeff_lut = 32'h40877E01;
            8'h91: coeff_lut = 32'h40827C6D;
            8'h92: coeff_lut = 32'h407AAA99;
            8'h93: coeff_lut = 32'h40701D08;
            8'h94: coeff_lut = 32'h40655C06;
            8'h95: coeff_lut = 32'h405A736B;
            8'h96: coeff_lut = 32'h404F6EFD;
            8'h97: coeff_lut = 32'h40445A55;
            8'h98: coeff_lut = 32'h403940D7;
            8'h99: coeff_lut = 32'h402E2D95;
            8'h9A: coeff_lut = 32'h40232B4B;
            8'h9B: coeff_lut = 32'h40184445;
            8'h9C: coeff_lut = 32'h400D825B;
            8'h9D: coeff_lut = 32'h4002EEDB;
            8'h9E: coeff_lut = 32'h3FF1250C;
            8'h9F: coeff_lut = 32'h3FDCEB02;
            8'hA0: coeff_lut = 32'h3FC93E9B;
            8'hA1: coeff_lut = 32'h3FB62D8A;
            8'hA2: coeff_lut = 32'h3FA3C422;
            8'hA3: coeff_lut = 32'h3F920D51;
            8'hA4: coeff_lut = 32'h3F81129A;
            8'hA5: coeff_lut = 32'h3F61B826;
            8'hA6: coeff_lut = 32'h3F42E0D4;
            8'hA7: coeff_lut = 32'h3F25A9C5;
            8'hA8: coeff_lut = 32'h3F0A1AC2;
            8'hA9: coeff_lut = 32'h3EE071C4;
            8'hAA: coeff_lut = 32'h3EB00D48;
            8'hAB: coeff_lut = 32'h3E83080F;
            8'hAC: coeff_lut = 32'h3E32BAA9;
            8'hAD: coeff_lut = 32'h3DCC0F61;
            8'hAE: coeff_lut = 32'h3CFEE250;
            8'hAF: coeff_lut = 32'hBCFF5213;
            8'hB0: coeff_lut = 32'hBDB2EBA8;
            8'hB1: coeff_lut = 32'hBE0CF6C6;
            8'hB2: coeff_lut = 32'hBE3AA340;
            8'hB3: coeff_lut = 32'hBE62B685;
            8'hB4: coeff_lut = 32'hBE82B81F;
            8'hB5: coeff_lut = 32'hBE9189E3;
            8'hB6: coeff_lut = 32'hBE9DF3C0;
            8'hB7: coeff_lut = 32'hBEA81A0F;
            8'hB8: coeff_lut = 32'hBEB02202;
            8'hB9: coeff_lut = 32'hBEB63152;
            8'hBA: coeff_lut = 32'hBEBA6DF0;
            8'hBB: coeff_lut = 32'hBEBCFDBC;
            8'hBC: coeff_lut = 32'hBEBE0643;
            8'hBD: coeff_lut = 32'hBEBDAC80;
            8'hBE: coeff_lut = 32'hBEBC149F;
            8'hBF: coeff_lut = 32'hBEB961CB;
            8'hC0: coeff_lut = 32'hBEB5B5FD;
            8'hC1: coeff_lut = 32'hBEB131D6;
            8'hC2: coeff_lut = 32'hBEABF47A;
            8'hC3: coeff_lut = 32'hBEA61B70;
            8'hC4: coeff_lut = 32'hBE9FC291;
            8'hC5: coeff_lut = 32'hBE9903EC;
            8'hC6: coeff_lut = 32'hBE91F7C2;
            8'hC7: coeff_lut = 32'hBE8AB476;
            8'hC8: coeff_lut = 32'hBE834E91;
            8'hC9: coeff_lut = 32'hBE77B179;
            8'hCA: coeff_lut = 32'hBE68C796;
            8'hCB: coeff_lut = 32'hBE59FD80;
            8'hCC: coeff_lut = 32'hBE4B6DBA;
            8'hCD: coeff_lut = 32'hBE3D2F60;
            8'hCE: coeff_lut = 32'hBE2F564D;
            8'hCF: coeff_lut = 32'hBE21F33E;
            8'hD0: coeff_lut = 32'hBE151405;
            8'hD1: coeff_lut = 32'hBE08C3B2;
            8'hD2: coeff_lut = 32'hBDFA158E;
            8'hD3: coeff_lut = 32'hBDE3DED5;
            8'hD4: coeff_lut = 32'hBDCEEB35;
            8'hD5: coeff_lut = 32'hBDBB3EBD;
            8'hD6: coeff_lut = 32'hBDA8D9FD;
            8'hD7: coeff_lut = 32'hBD97BA66;
            8'hD8: coeff_lut = 32'hBD87DAAE;
            8'hD9: coeff_lut = 32'hBD72665E;
            8'hDA: coeff_lut = 32'hBD577487;
            8'hDB: coeff_lut = 32'hBD3EC93A;
            8'hDC: coeff_lut = 32'hBD284B27;
            8'hDD: coeff_lut = 32'hBD13DEEA;
            8'hDE: coeff_lut = 32'hBD01678E;
            8'hDF: coeff_lut = 32'hBCE18E30;
            8'hE0: coeff_lut = 32'hBCC3BDE6;
            8'hE1: coeff_lut = 32'hBCA920B6;
            8'hE2: coeff_lut = 32'hBC917974;
            8'hE3: coeff_lut = 32'hBC7917DB;
            8'hE4: coeff_lut = 32'hBC543AE8;
            8'hE5: coeff_lut = 32'hBC33EAA3;
            8'hE6: coeff_lut = 32'hBC17BA48;
            8'hE7: coeff_lut = 32'hBBFE84B5;
            8'hE8: coeff_lut = 32'hBBD44222;
            8'hE9: coeff_lut = 32'hBBAFF555;
            8'hEA: coeff_lut = 32'hBB90F33F;
            8'hEB: coeff_lut = 32'hBB6D3AE8;
            8'hEC: coeff_lut = 32'hBB40C459;
            8'hED: coeff_lut = 32'hBB1B7833;
            8'hEE: coeff_lut = 32'hBAF8C861;
            8'hEF: coeff_lut = 32'hBAC55B12;
            8'hF0: coeff_lut = 32'hBA9B1FD7;
            8'hF1: coeff_lut = 32'hBA717137;
            8'hF2: coeff_lut = 32'hBA39DE7E;
            8'hF3: coeff_lut = 32'hBA0D65B2;
            8'hF4: coeff_lut = 32'hB9D4538B;
            8'hF5: coeff_lut = 32'hB99D1965;
            8'hF6: coeff_lut = 32'hB964A754;
            8'hF7: coeff_lut = 32'hB9234187;
            8'hF8: coeff_lut = 32'hB8E3F0F2;
            8'hF9: coeff_lut = 32'hB89AD333;
            8'hFA: coeff_lut = 32'hB84B18F4;
            8'hFB: coeff_lut = 32'hB7FE172A;
            8'hFC: coeff_lut = 32'hB7944341;
            8'hFD: coeff_lut = 32'hB71A7245;
            8'hFE: coeff_lut = 32'hB6818BC5;
            8'hFF: coeff_lut = 32'hB579E2A7;
        endcase
    end
endfunction

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy      <= 1'b0;
        out_coeff <= 32'h00000000;
    end else if (input_accepted) begin
        busy      <= 1'b1;
        out_coeff <= coeff_lut(in_index);
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
