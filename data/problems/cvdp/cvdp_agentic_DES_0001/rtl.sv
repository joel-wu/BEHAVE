module des_enc #(
    parameter int NBW_DATA=64,
    parameter int NBW_KEY=64
) (
    input logic clk, rst_async_n, i_valid,
    input logic [1:NBW_DATA] i_data,
    input logic [1:NBW_KEY] i_key,
    output logic o_valid,
    output logic [1:NBW_DATA] o_data
);
    function automatic [63:0] ip(input [63:0] v);
        ip={v[6],v[14],v[22],v[30],v[38],v[46],v[54],v[62],v[4],v[12],v[20],v[28],v[36],v[44],v[52],v[60],v[2],v[10],v[18],v[26],v[34],v[42],v[50],v[58],v[0],v[8],v[16],v[24],v[32],v[40],v[48],v[56],v[7],v[15],v[23],v[31],v[39],v[47],v[55],v[63],v[5],v[13],v[21],v[29],v[37],v[45],v[53],v[61],v[3],v[11],v[19],v[27],v[35],v[43],v[51],v[59],v[1],v[9],v[17],v[25],v[33],v[41],v[49],v[57]};
    endfunction
    function automatic [63:0] fp(input [63:0] v);
        fp={v[24],v[56],v[16],v[48],v[8],v[40],v[0],v[32],v[25],v[57],v[17],v[49],v[9],v[41],v[1],v[33],v[26],v[58],v[18],v[50],v[10],v[42],v[2],v[34],v[27],v[59],v[19],v[51],v[11],v[43],v[3],v[35],v[28],v[60],v[20],v[52],v[12],v[44],v[4],v[36],v[29],v[61],v[21],v[53],v[13],v[45],v[5],v[37],v[30],v[62],v[22],v[54],v[14],v[46],v[6],v[38],v[31],v[63],v[23],v[55],v[15],v[47],v[7],v[39]};
    endfunction
    function automatic [47:0] e(input [31:0] v);
        e={v[0],v[31],v[30],v[29],v[28],v[27],v[28],v[27],v[26],v[25],v[24],v[23],v[24],v[23],v[22],v[21],v[20],v[19],v[20],v[19],v[18],v[17],v[16],v[15],v[16],v[15],v[14],v[13],v[12],v[11],v[12],v[11],v[10],v[9],v[8],v[7],v[8],v[7],v[6],v[5],v[4],v[3],v[4],v[3],v[2],v[1],v[0],v[31]};
    endfunction
    function automatic [31:0] p(input [31:0] v);
        p={v[16],v[25],v[12],v[11],v[3],v[20],v[4],v[15],v[31],v[17],v[9],v[6],v[27],v[14],v[1],v[22],v[30],v[24],v[8],v[18],v[0],v[5],v[29],v[23],v[13],v[19],v[2],v[26],v[10],v[21],v[28],v[7]};
    endfunction
    function automatic [27:0] pc1_c(input [63:0] v);
        pc1_c={v[7],v[15],v[23],v[31],v[39],v[47],v[55],v[63],v[6],v[14],v[22],v[30],v[38],v[46],v[54],v[62],v[5],v[13],v[21],v[29],v[37],v[45],v[53],v[61],v[4],v[12],v[20],v[28]};
    endfunction
    function automatic [27:0] pc1_d(input [63:0] v);
        pc1_d={v[1],v[9],v[17],v[25],v[33],v[41],v[49],v[57],v[2],v[10],v[18],v[26],v[34],v[42],v[50],v[58],v[3],v[11],v[19],v[27],v[35],v[43],v[51],v[59],v[36],v[44],v[52],v[60]};
    endfunction
    function automatic [47:0] pc2(input [55:0] v);
        pc2={v[42],v[39],v[45],v[32],v[55],v[51],v[53],v[28],v[41],v[50],v[35],v[46],v[33],v[37],v[44],v[52],v[30],v[48],v[40],v[49],v[29],v[36],v[43],v[54],v[15],v[4],v[25],v[19],v[9],v[1],v[26],v[16],v[5],v[11],v[23],v[8],v[12],v[7],v[17],v[0],v[22],v[3],v[10],v[14],v[6],v[20],v[27],v[24]};
    endfunction
    function automatic [3:0] s1(input [5:0] b);
        reg [255:0] lut;
        begin lut=256'hd0650aa3e739bc5f7b12964d288ec1f487305995bcc66aa318db2fe2417df40e; s1=lut[4*b +: 4]; end
    endfunction
    function automatic [3:0] s2(input [5:0] b);
        reg [255:0] lut;
        begin lut=256'h9fe25309c67c68b5214df43a1ba78ed05ab5906cad1207c9e4832bf67e48d13f; s2=lut[4*b +: 4]; end
    endfunction
    function automatic [3:0] s3(input [5:0] b);
        reg [255:0] lut;
        begin lut=256'hc72e5ab53ce2f14b70839f6809d4a61d18f2b4cbe75c8d21a56f43369e0970da; s3=lut[4*b +: 4]; end
    endfunction
    function automatic [3:0] s4(input [5:0] b);
        reg [255:0] lut;
        begin lut=256'he42872c5be53419f8dd71bac6009f63a9fe4ac1bc52872413a09f66053be8dd7; s4=lut[4*b +: 4]; end
    endfunction
    function automatic [3:0] s5(input [5:0] b);
        reg [255:0] lut;
        begin lut=256'h3e5043a6950cf96fd827ed1a7bc182b4698e903daff3055816db7a47c124bce2; s5=lut[4*b +: 4]; end
    endfunction
    function automatic [3:0] s6(input [5:0] b);
        reg [255:0] lut;
        begin lut=256'hd68b0d617a14e0b7a3fc5892c52f3e498b35b70ee4d31d605896c2792f4af1ac; s6=lut[4*b +: 4]; end
    endfunction
    function automatic [3:0] s7(input [5:0] b);
        reg [255:0] lut;
        begin lut=256'hc23925e0f8065f9a7ea7431c8ddbb4616186fa25c7593ce3ad18904f7eb20bd4; s7=lut[4*b +: 4]; end
    endfunction
    function automatic [3:0] s8(input [5:0] b);
        reg [255:0] lut;
        begin lut=256'hb865533f0d9ac6f0d28eac4971e41b27279ce005be6359ca417b3fa684d8f21d; s8=lut[4*b +: 4]; end
    endfunction
    function automatic [27:0] rotate28(input [27:0] v, input integer amount);
        rotate28=(v << amount) | (v >> (28-amount));
    endfunction
    function automatic [31:0] feistel(input [31:0] r, input [47:0] key);
        reg [47:0] mixed;
        reg [31:0] boxes;
        begin
            mixed=e(r)^key;
            boxes={s1(mixed[47:42]),s2(mixed[41:36]),s3(mixed[35:30]),s4(mixed[29:24]),
                   s5(mixed[23:18]),s6(mixed[17:12]),s7(mixed[11:6]),s8(mixed[5:0])};
            feistel=p(boxes);
        end
    endfunction
    logic [31:0] left_q[0:15], right_q[0:15];
    logic [27:0] c_q[0:15], d_q[0:15];
    logic [15:0] valid_q;
    wire [63:0] permuted=ip(i_data);
    assign o_valid=valid_q[15];
    assign o_data=valid_q[15] ? fp({right_q[15],left_q[15]}) : 64'b0;
    for(genvar n=0;n<16;n++) begin: rounds
        localparam integer SHIFT=(n==0 || n==1 || n==8 || n==15) ? 1 : 2;
        wire [27:0] old_c, old_d, next_c, next_d;
        wire [31:0] old_l, old_r;
        wire valid_in;
        if(n==0) begin
            assign old_c=pc1_c(i_key); assign old_d=pc1_d(i_key);
            assign old_l=permuted[63:32]; assign old_r=permuted[31:0];
            assign valid_in=i_valid;
        end else begin
            assign old_c=c_q[n-1]; assign old_d=d_q[n-1];
            assign old_l=left_q[n-1]; assign old_r=right_q[n-1];
            assign valid_in=valid_q[n-1];
        end
        assign next_c=rotate28(old_c,SHIFT); assign next_d=rotate28(old_d,SHIFT);
        always_ff @(posedge clk or negedge rst_async_n) begin
            if(!rst_async_n) begin
                left_q[n]<=0; right_q[n]<=0; c_q[n]<=0; d_q[n]<=0; valid_q[n]<=0;
            end else begin
                valid_q[n]<=valid_in;
                left_q[n]<=valid_in ? old_r : 32'b0;
                right_q[n]<=valid_in ? old_l^feistel(old_r,pc2({next_c,next_d})) : 32'b0;
                c_q[n]<=valid_in ? next_c : 28'b0;
                d_q[n]<=valid_in ? next_d : 28'b0;
            end
        end
    end
endmodule
